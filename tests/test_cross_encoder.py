"""Executes the cross-encoder: input format, truncation, loss, training step.

Built on a tiny randomly-initialised encoder rather than the real pretrained
one. That is deliberate: these tests check the plumbing the spec pins down --
the pair format, the truncation order, the listwise loss, that gradients flow
through all layers -- none of which depends on the weights. Accuracy is the
gold set's job, not a unit test's.

Skipped when torch is absent, which is why the suite still runs with only
numpy installed.
"""

from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("transformers")

from ml.config import CROSS_ENCODER_MAX_TOKENS, TrainingConfig  # noqa: E402
from ml.ranking.cross_encoder import (  # noqa: E402
    CrossEncoderGroup,
    check_language_coverage,
    language_token,
    linear_warmup_schedule,
    listwise_softmax_loss,
    mine_hard_negatives,
    train_cross_encoder,
)
from ml.schemas import Candidate, Entry, EntryType  # noqa: E402
from ml.templates.cross_encoder import (  # noqa: E402
    TemplateCrossEncoder,
    template_head,
    type_token,
)

HIDDEN = 64


@pytest.fixture(scope="module")
def tiny_tokenizer():
    """A small word-level tokenizer, so no pretrained download is needed."""
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import PreTrainedTokenizerFast

    words = (
        "scaffold a new single page app with routing react vite starter eslint "
        "fastapi service auth migrations python javascript import from export "
        "function const let return class def framework template bundle helper"
    ).split()
    vocab = {tok: i for i, tok in enumerate(["[PAD]", "[UNK]", "[CLS]", "[SEP]", *dict.fromkeys(words)])}
    backend = Tokenizer(WordLevel(vocab, unk_token="[UNK]"))
    backend.pre_tokenizer = Whitespace()
    return PreTrainedTokenizerFast(
        tokenizer_object=backend,
        cls_token="[CLS]",
        sep_token="[SEP]",
        pad_token="[PAD]",
        unk_token="[UNK]",
    )


def build_model(tiny_tokenizer, hidden: int = HIDDEN, layers: int = 2) -> TemplateCrossEncoder:
    from transformers import BertConfig, BertModel

    config = BertConfig(
        vocab_size=len(tiny_tokenizer) + 32,
        hidden_size=hidden,
        num_hidden_layers=layers,
        num_attention_heads=2,
        intermediate_size=hidden * 2,
        max_position_embeddings=CROSS_ENCODER_MAX_TOKENS,
    )
    return TemplateCrossEncoder(
        tokenizer=tiny_tokenizer,
        encoder=BertModel(config),
        check_languages=False,
    )


@pytest.fixture
def model(tiny_tokenizer) -> TemplateCrossEncoder:
    torch.manual_seed(0)
    return build_model(tiny_tokenizer)


def template(entry_id: str, *, code_lines: int = 5, description: str = "react vite starter") -> Entry:
    return Entry(
        id=entry_id,
        type=EntryType.TEMPLATE,
        name=entry_id,
        description=description,
        category="scaffolding",
        language="javascript",
        framework="react",
        code="import react from vite\n" * code_lines,
        members=9,
    )


class TestHeadArchitecture:
    def test_head_matches_the_spec_layer_table(self, tiny_tokenizer):
        """768 -> dropout -> 256 -> GELU -> dropout -> 1."""
        from torch import nn

        wide = build_model(tiny_tokenizer, hidden=768, layers=1)
        kinds = [type(layer) for layer in wide.head]
        assert kinds == [nn.Dropout, nn.Linear, nn.GELU, nn.Dropout, nn.Linear]
        assert (wide.head[1].in_features, wide.head[1].out_features) == (768, 256)
        assert (wide.head[4].in_features, wide.head[4].out_features) == (256, 1)
        assert wide.head[0].p == pytest.approx(0.1)
        assert wide.head[3].p == pytest.approx(0.1)

    def test_forward_returns_one_logit_per_pair(self, model):
        pairs = [model.tokenizer.encode("scaffold a new app", template(f"t{i}")) for i in range(4)]
        batch = model.tokenizer.collate(pairs)
        logits = model(batch["input_ids"], batch["attention_mask"])
        assert logits.shape == (4,)
        assert torch.isfinite(logits).all()

    def test_logit_is_not_squashed(self, model):
        """Stage 3 consumes a raw logit; a sigmoid here would fake calibration."""
        pairs = [model.tokenizer.encode("scaffold", template("t0"))]
        batch = model.tokenizer.collate(pairs)
        with torch.no_grad():
            model.head[4].bias.fill_(12.0)
            logits = model(batch["input_ids"], batch["attention_mask"])
        assert logits.item() > 1.0, "a raw logit may exceed 1; this looks squashed"


class TestInputFormat:
    def test_pair_is_cls_query_sep_candidate_sep(self, model):
        pair = model.tokenizer.encode("scaffold a new app", template("t0"))
        ids = pair.input_ids
        tok = model.tokenizer.tokenizer
        assert ids[0] == tok.cls_token_id
        assert ids[-1] == tok.sep_token_id
        assert ids.count(tok.sep_token_id) == 2
        assert len(pair.attention_mask) == len(ids)

    def test_language_and_type_tokens_are_single_tokens(self, model):
        tok = model.tokenizer.tokenizer
        for marker in (language_token("cuda"), type_token(template("t0")), "<type:bundle>"):
            assert len(tok(marker, add_special_tokens=False)["input_ids"]) == 1, marker

    def test_template_candidate_carries_no_signature(self, model):
        """A template is not called, so its signature never enters the input.

        Asserted as an equality rather than an absence: setting a signature
        must leave the encoded pair byte-identical. Searching the token ids for
        the signature's words would pass for the wrong reason, since unknown
        words all collapse to the same [UNK] id.
        """
        entry = template("t0")
        without_signature = model.tokenizer.encode("scaffold", entry).input_ids

        entry.signature = "(should, never, appear)"
        assert "never" not in template_head(entry)
        assert model.tokenizer.encode("scaffold", entry).input_ids == without_signature

    def test_truncates_to_the_window(self, model):
        huge = template("t0", code_lines=4000, description="react vite starter " * 400)
        pair = model.tokenizer.encode("scaffold a new app " * 200, huge)
        assert len(pair.input_ids) <= CROSS_ENCODER_MAX_TOKENS

    def test_code_floor_survives_a_verbose_description(self, model):
        """A long blurb must not crowd the scaffold out of the window."""
        verbose = template("t0", code_lines=300, description="react vite starter eslint " * 300)
        pair = model.tokenizer.encode("scaffold", verbose)
        head_cap = model.tokenizer.head_max_tokens
        # Everything past [CLS] query [SEP] markers + head must be code.
        assert len(pair.input_ids) > head_cap + 10

    def test_batch_pads_to_the_longest_member_not_the_window(self, model):
        pairs = [
            model.tokenizer.encode("scaffold", template("short", code_lines=1)),
            model.tokenizer.encode("scaffold", template("long", code_lines=6)),
        ]
        batch = model.tokenizer.collate(pairs)
        assert batch["input_ids"].shape[1] == max(len(p.input_ids) for p in pairs)
        assert batch["input_ids"].shape[1] < CROSS_ENCODER_MAX_TOKENS
        assert batch["attention_mask"][0].sum().item() == len(pairs[0].input_ids)


class TestScoring:
    def test_score_candidates_writes_back_onto_candidates(self, model):
        candidates = [Candidate(entry=template(f"t{i}")) for i in range(3)]
        scores = model.score_candidates("scaffold a new app", candidates)
        assert scores.shape == (3,)
        assert all(c.cross_encoder_score is not None for c in candidates)
        assert [c.cross_encoder_score for c in candidates] == pytest.approx(scores.tolist())

    def test_scoring_leaves_the_model_in_eval_free_state(self, model):
        model.train()
        model.score_candidates("scaffold", [Candidate(entry=template("t0"))])
        assert model.training, "score_candidates must restore the previous mode"

    def test_empty_candidate_list(self, model):
        assert model.score_candidates("scaffold", []).shape == (0,)


class TestLoss:
    def test_lower_when_the_positive_leads(self):
        good = torch.tensor([[5.0, 0.0, 0.0, 0.0]])
        bad = torch.tensor([[0.0, 5.0, 0.0, 0.0]])
        assert listwise_softmax_loss(good).item() < listwise_softmax_loss(bad).item()

    def test_masked_padding_cannot_compete(self):
        """-inf slots must not absorb probability mass."""
        padded = torch.tensor([[2.0, 1.0, float("-inf")]])
        unpadded = torch.tensor([[2.0, 1.0]])
        assert listwise_softmax_loss(padded).item() == pytest.approx(
            listwise_softmax_loss(unpadded).item(), abs=1e-6
        )

    def test_rejects_wrong_shape(self):
        with pytest.raises(ValueError, match="group_size"):
            listwise_softmax_loss(torch.zeros(2, 3, 4))


class TestTraining:
    def test_warmup_then_decay(self):
        param = torch.nn.Parameter(torch.zeros(1))
        optimizer = torch.optim.AdamW([param], lr=2e-5)
        scheduler = linear_warmup_schedule(optimizer, total_steps=100, warmup_fraction=0.1)
        first = optimizer.param_groups[0]["lr"]
        seen = [first]
        for _ in range(100):
            optimizer.step()  # before scheduler.step(), or torch warns
            scheduler.step()
            seen.append(optimizer.param_groups[0]["lr"])
        assert first == pytest.approx(0.0), "warmup must start at zero"
        assert max(seen) == pytest.approx(2e-5, rel=1e-6), "peak is the configured lr"
        assert seen[-1] < seen[10], "lr must decay after warmup"

    def test_gradients_reach_every_layer(self, model):
        pairs = [model.tokenizer.encode("scaffold", template(f"t{i}")) for i in range(2)]
        batch = model.tokenizer.collate(pairs)
        logits = model(batch["input_ids"], batch["attention_mask"])
        listwise_softmax_loss(logits.unsqueeze(0)).backward()
        missing = [name for name, p in model.named_parameters() if p.requires_grad and p.grad is None]
        # Pooler params are unused by design: the head reads [CLS] directly.
        missing = [name for name in missing if "pooler" not in name]
        assert not missing, f"no gradient reached: {missing[:5]}"

    def test_training_reduces_loss_on_a_separable_task(self, tiny_tokenizer):
        """One positive the query shares words with, negatives it does not.

        A randomly initialised model has no reason to prefer either, so a
        falling loss is evidence the optimiser, schedule and loss are wired
        together -- not that the architecture is good.
        """
        torch.manual_seed(0)
        model = build_model(tiny_tokenizer)
        groups = [
            CrossEncoderGroup(
                query_text="scaffold a new single page app with routing",
                positive=template("good", description="single page app routing"),
                negatives=(
                    template("bad1", description="fastapi service auth migrations"),
                    template("bad2", description="python class def return"),
                ),
            )
        ] * 6

        # lr and epochs are far above the spec's 2e-5: that value is for
        # fine-tuning a real pretrained 768-wide, 12-layer encoder, where the
        # weights already encode the language. This model is 64-wide, 2-layer
        # and random, and at 2e-5 over 12 steps it never leaves chance
        # (ln 3 = 1.0986 for a three-item group) -- which says nothing about
        # whether the loss and schedule are wired together, so the test turns
        # the dial up until it does.
        history = train_cross_encoder(
            model,
            groups,
            config=TrainingConfig(epochs=10, batch_groups=2, learning_rate=2e-3, warmup_fraction=0.1),
            device="cpu",
        )
        assert len(history) == 10 * 3
        assert all(np.isfinite(history))
        assert history[-1] < history[0], f"loss did not fall: {history[0]:.4f} -> {history[-1]:.4f}"

    def test_rejects_empty_training_set(self, model):
        with pytest.raises(ValueError, match="no training groups"):
            train_cross_encoder(model, [], device="cpu")


class TestHardNegatives:
    def test_excludes_the_positive_and_caps_the_count(self):
        retrieved = [Candidate(entry=template(f"t{i}")) for i in range(20)]
        negatives = mine_hard_negatives(retrieved, ["t3"], count=15)
        assert len(negatives) == 15
        assert "t3" not in {e.id for e in negatives}

    def test_takes_the_top_of_the_retrieval_list(self):
        """Hard negatives are the look-alikes retrieval ranked highly."""
        retrieved = [Candidate(entry=template(f"t{i}")) for i in range(20)]
        negatives = mine_hard_negatives(retrieved, ["t0"], count=3)
        assert [e.id for e in negatives] == ["t1", "t2", "t3"]


class TestLanguageCoverage:
    def test_names_the_missing_mvp_languages(self):
        with pytest.warns(UserWarning, match="cpp"):
            missing = check_language_coverage({"python", "javascript"})
        assert set(missing) == {"html", "css", "cpp", "cuda"}

    def test_full_coverage_does_not_warn(self):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert check_language_coverage(
                {"javascript", "html", "css", "python", "cpp", "cuda"}
            ) == ()
