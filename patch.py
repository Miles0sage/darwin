#!/usr/bin/env python3
"""
Darwin Patch Recipes — cached LibCST transformer functions.

Instead of caching a regex substitution (which only matches identical code),
Darwin caches a LibCST CSTTransformer *source* that encodes a structural
rewrite. At runtime we:
  1. Parse the new (different) codebase into a CST.
  2. Instantiate the cached transformer.
  3. Apply it deterministically — no LLM call on cache hit.
  4. Emit new source; pipe through AST safety gate.

If the cached transformer's visitors don't match any node in the new source
("pattern miss"), we fall through to the B-path (LLM adapter) with the
cached diagnosis as context.

Key insight per Apr 22 factory_swarm consensus: "compile LLM intelligence
into deterministic code artifacts". The LLM fires ONCE at first-diagnose,
produces a transformer function, and every subsequent cross-repo hit is
an O(1) CST rewrite — no LLM latency, no prompt-cache critique.

SECURITY DISCLAIMER (called out explicitly, do not oversell):
  The `compile_transformer` namespace is NAMESPACE ISOLATION, not a
  SECURITY SANDBOX. A hostile transformer source can escape via
  `type.__subclasses__()`, `__import__` lookups on captured frames, or
  attribute walks starting from any exposed class. Darwin trusts that
  (a) cache writes come only from its own diagnose path, which funnels
  through the AST safety gate, and (b) transformer sources are reviewed
  before being accepted into a shared blackboard. Do NOT load a
  third-party blackboard's recipes without a human review step.
"""

from __future__ import annotations

import textwrap
import traceback
from dataclasses import dataclass

import libcst as cst

import builtins as _py_builtins

# Restricted builtin set. Everything a pure CST visitor needs to construct
# a class and walk nodes — no open(), no subprocess.
# __import__ is included so that `import libcst as cst` inside transformer
# source resolves (libcst is already injected into the exec namespace).
_SAFE_BUILTIN_NAMES = (
    "__build_class__", "__name__", "__import__",  # required for class/import syntax
    "isinstance", "issubclass", "type", "object",
    "len", "range", "enumerate", "map", "filter", "zip",
    "True", "False", "None", "Exception", "ValueError", "TypeError",
    "str", "int", "float", "bool", "list", "tuple", "dict", "set", "frozenset",
    "getattr", "hasattr", "setattr",
    "print", "repr",
)
_SAFE_BUILTINS = {name: getattr(_py_builtins, name, None) for name in _SAFE_BUILTIN_NAMES}


@dataclass
class PatchRecipe:
    """A cached rewrite recipe.

    `transformer_src` is the source of a `libcst.CSTTransformer` subclass
    called `Patch` (convention). `fallback_source_patch` is the original
    full-file rewrite the LLM produced — used if the transformer pattern-
    misses in a new repo (fall through to B-path).
    """

    transformer_src: str
    fallback_source_patch: str | None = None


class PatchMissError(Exception):
    """The cached transformer produced no changes — pattern did not match."""


def compile_transformer(transformer_src: str) -> type[cst.CSTTransformer]:
    """Exec the transformer source in a sandboxed namespace; return the class."""
    ns: dict = {
        "__builtins__": _SAFE_BUILTINS,
        "cst": cst,
        "libcst": cst,
    }
    # textwrap.dedent tolerates transformer source quoted with leading whitespace
    exec(textwrap.dedent(transformer_src), ns)
    patch_cls = ns.get("Patch")
    if patch_cls is None:
        raise ValueError(
            "Transformer source must define a class named `Patch` subclassing "
            "libcst.CSTTransformer."
        )
    if not issubclass(patch_cls, cst.CSTTransformer):
        raise TypeError("`Patch` must subclass libcst.CSTTransformer.")
    return patch_cls


def apply_recipe(source_code: str, recipe: PatchRecipe) -> str:
    """Apply the cached transformer deterministically. No LLM call.

    Raises PatchMissError if the transformer didn't change anything
    (meaning the new codebase has a different structure — fall through to
    the B-path adapter).
    """
    patch_cls = compile_transformer(recipe.transformer_src)
    original_tree = cst.parse_module(source_code)
    transformer = patch_cls()
    new_tree = original_tree.visit(transformer)
    new_code = new_tree.code

    if new_code == source_code:
        raise PatchMissError(
            "Transformer produced no changes — pattern did not match this codebase."
        )
    return new_code


def try_apply(source_code: str, recipe: PatchRecipe) -> tuple[bool, str, str | None]:
    """Convenience: (applied, new_source_or_original, error_or_None)."""
    try:
        return True, apply_recipe(source_code, recipe), None
    except PatchMissError as e:
        return False, source_code, str(e)
    except Exception as e:  # noqa: BLE001
        return False, source_code, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}"


# Reference transformer sources. These are what the LLM is asked to mimic.
# They handle the 3 canned scenarios using CST visitors — structural, not
# text-based — so they match across different variable names and layouts.

REFERENCE_SCHEMA_CHANGE = '''
class Patch(cst.CSTTransformer):
    """Cross-repo transformer for `X["text"]` → nested-safe lookup.

    Matches any `X["text"]` or `X['text']` subscript regardless of variable
    name, rewrites to `(X.get("data", {}).get("text") or X.get("text", ""))`.
    """

    def leave_Subscript(self, original_node, updated_node):
        if len(updated_node.slice) != 1:
            return updated_node
        elt = updated_node.slice[0]
        if not isinstance(elt, cst.SubscriptElement) or not isinstance(elt.slice, cst.Index):
            return updated_node
        key = elt.slice.value
        if not (isinstance(key, cst.SimpleString) and key.value in ('"text"', "'text'")):
            return updated_node
        receiver_code = cst.Module([]).code_for_node(updated_node.value)
        return cst.parse_expression(
            f'({receiver_code}.get("data", {{}}).get("text") or {receiver_code}.get("text", ""))'
        )
'''.strip()


REFERENCE_MISSING_FILE = '''
class Patch(cst.CSTTransformer):
    """Rewrite assignment of a `/`-chained path that ends in "v3"/"data.json"
    to a conditional fallback to "v1" if the v3 file does not exist.

    Structural match: walks the left-associative BinaryOperation chain,
    collects SimpleString operands, and fires only when the token set
    contains both "v3" and "data.json". This avoids string-level matching
    on generated code.
    """

    def _collect_path_tokens(self, node):
        """Walk left-assoc `/` chain; return list of string tokens or None."""
        tokens = []
        cur = node
        while isinstance(cur, cst.BinaryOperation) and isinstance(cur.operator, cst.Divide):
            if isinstance(cur.right, cst.SimpleString):
                tokens.insert(0, cur.right.value.strip('"').strip("'"))
            else:
                return None
            cur = cur.left
        # Leftmost should be the BASE_DIR Name; accept anything (caller's prerogative).
        return tokens

    def leave_Assign(self, original_node, updated_node):
        if len(updated_node.targets) != 1:
            return updated_node
        tokens = self._collect_path_tokens(updated_node.value)
        if tokens is None:
            return updated_node
        if "v3" not in tokens or "data.json" not in tokens:
            return updated_node
        # Structural rebuild: swap the "v3" SimpleString for an IfExp that
        # picks "v3" or "v1" based on existence. We rewrite the `/`-chain
        # in-place by visiting the BinaryOperation subtree.
        class _V3Swap(cst.CSTTransformer):
            def leave_SimpleString(self, _o, u):
                if u.value not in ('"v3"', "'v3'"):
                    return u
                # Replace with: ("v3" if (BASE_DIR / "api" / "v3" / "data.json").exists() else "v1")
                return cst.parse_expression(
                    '("v3" if (BASE_DIR / "api" / "v3" / "data.json").exists() else "v1")'
                )
        new_value = updated_node.value.visit(_V3Swap())
        return updated_node.with_changes(value=new_value)
'''.strip()


REFERENCE_RATE_LIMIT = '''
class Patch(cst.CSTTransformer):
    """Remove the `raise RuntimeError("API rate limited (429)")` placeholder
    AND inject an auditable warning so the removal is visible in logs.

    Rationale: silent-masking the raise behavior-alters the function in a
    way the AST safety gate cannot detect. Emitting a `warnings.warn` leaves
    a trace so an SRE spots "Darwin removed rate-limit placeholder" in
    stderr rather than discovering it via regression.
    """

    def leave_Raise(self, original_node, updated_node):
        exc = updated_node.exc
        if exc is None:
            return updated_node
        code = cst.Module([]).code_for_node(exc)
        if "API rate limited (429)" not in code:
            return updated_node
        return cst.parse_statement(
            'import warnings; warnings.warn('
            '"Darwin removed rate-limit placeholder — verify upstream client has retry/backoff."'
            ')'
        )
'''.strip()


# Map (error_class → reference transformer). Used as seed recipes; LLM can
# replace these with bespoke transformers at first-diagnose time.
REFERENCE_TRANSFORMERS: dict[str, str] = {
    "KeyError": REFERENCE_SCHEMA_CHANGE,
    "FileNotFoundError": REFERENCE_MISSING_FILE,
    "RuntimeError": REFERENCE_RATE_LIMIT,
}


def reference_recipe_for(error_class: str | None) -> PatchRecipe | None:
    """Return a seed recipe for a given error class, or None."""
    if error_class is None:
        return None
    src = REFERENCE_TRANSFORMERS.get(error_class)
    return PatchRecipe(transformer_src=src) if src else None


def export_recipe(blackboard_entry: dict, repo_id: str) -> dict:
    """Bundle a blackboard entry for Crossfeed transport.

    Extracts transformer_src and fingerprint, computes ast_signature_hash
    (SHA-256 of transformer_src), and hashes repo_id for privacy.
    Raw source code is NOT included — only the AST transformer pattern.
    """
    import hashlib as _hashlib

    transformer_src: str = blackboard_entry.get("transformer_src", "")
    fingerprint: str = blackboard_entry.get("fingerprint", "")
    ast_signature_hash = _hashlib.sha256(transformer_src.encode("utf-8")).hexdigest()
    repo_id_hashed = _hashlib.sha256(repo_id.encode("utf-8")).hexdigest()
    return {
        "fingerprint": fingerprint,
        "transformer_src": transformer_src,
        "ast_signature_hash": ast_signature_hash,
        "repo_id_hashed": repo_id_hashed,
        "success_count": blackboard_entry.get("success_count", 0),
        "q_value": blackboard_entry.get("q_value", 0.0),
    }


def apply_recipe_from_crossfeed(
    source_code: str,
    crossfeed_msg: dict,
) -> tuple[bool, str, str | None]:
    """Apply a transformer recipe received from Crossfeed.

    Extracts transformer_src from crossfeed_msg["patch_recipe"], wraps it
    in a PatchRecipe, and delegates to try_apply().

    When DARWIN_WHITELIST_ENFORCE=1, the recipe must match a whitelist entry
    (fingerprint + ast_signature_hash); otherwise it is rejected.

    Returns:
        (success, transformed_source_or_original, error_or_None)
    """
    import hashlib as _hashlib
    from whitelist import Whitelist, enforcement_enabled, WHITELIST_PATH

    transformer_src: str = crossfeed_msg.get("patch_recipe", "")
    if not transformer_src:
        return False, source_code, "crossfeed_msg missing 'patch_recipe'"

    if enforcement_enabled():
        fingerprint = crossfeed_msg.get("fingerprint", "")
        ast_sig = _hashlib.sha256(transformer_src.encode("utf-8")).hexdigest()
        wl = Whitelist().load(WHITELIST_PATH)
        if not wl.is_approved(fingerprint, ast_sig):
            return False, source_code, "rejected: not in whitelist"

    recipe = PatchRecipe(transformer_src=transformer_src)
    return try_apply(source_code, recipe)


__all__ = [
    "PatchRecipe",
    "PatchMissError",
    "compile_transformer",
    "apply_recipe",
    "try_apply",
    "reference_recipe_for",
    "REFERENCE_SCHEMA_CHANGE",
    "REFERENCE_MISSING_FILE",
    "REFERENCE_RATE_LIMIT",
    "REFERENCE_TRANSFORMERS",
    "export_recipe",
    "apply_recipe_from_crossfeed",
]
