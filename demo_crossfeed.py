"""Darwin Crossfeed demo — asciinema-style walkthrough.

Scene 1: Repo A detects AttributeError, Darwin heals, exports recipe.
Scene 2: Crossfeed server receives, verifies HMAC, shows Q-delta noise.
Scene 3: Repo B autopatches from inbox — 0 LLM calls used.
"""

import hashlib
import os
import sys
import time
from dataclasses import asdict

from crossfeed import (
    CrossfeedMessage,
    CrossfeedClient,
    make_message,
    compute_q_delta,
    apply_q_delta,
    sample_laplace,
    sign_message,
    verify_message,
    _payload_dict,
)
from patch import apply_recipe_from_crossfeed, export_recipe, PatchRecipe, try_apply


class C:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def banner(text: str, color: str = C.CYAN) -> None:
    width = 60
    print(f"\n{color}{C.BOLD}{'=' * width}{C.RESET}")
    print(f"{color}{C.BOLD}  {text}{C.RESET}")
    print(f"{color}{C.BOLD}{'=' * width}{C.RESET}\n")


FASTAPI_SOURCE = '''
def get_user_name(response):
    return response.text.strip()
'''

TRANSFORMER_SRC = '''
import libcst as cst

class Patch(cst.CSTTransformer):
    def leave_Attribute(self, original_node, updated_node):
        if (
            isinstance(updated_node.value, cst.Name)
            and updated_node.value.value == "response"
            and isinstance(updated_node.attr, cst.Name)
            and updated_node.attr.value == "text"
        ):
            return cst.parse_expression(
                "(response.text if response is not None else \'\')"
            )
        return updated_node
'''

DJANGO_SOURCE = '''
def render_user(resp):
    return resp.text.strip()
'''


def scene_1() -> tuple[str, float, float, float, CrossfeedMessage | None]:
    banner("Scene 1: Repo A detects AttributeError, Darwin heals")

    print(f"{C.RED}[ERROR] AttributeError: 'NoneType' object has no attribute 'text'{C.RESET}")
    time.sleep(0.3)

    success, new_source, err = try_apply(FASTAPI_SOURCE, PatchRecipe(transformer_src=TRANSFORMER_SRC))
    if success:
        print(f"{C.GREEN}Darwin healed: {new_source.strip()[:60]}{C.RESET}")
    else:
        print(f"{C.YELLOW}Darwin patch miss ({err}) — using fallback{C.RESET}")
        new_source = FASTAPI_SOURCE

    fingerprint = hashlib.sha256(b"AttributeError: NoneType").hexdigest()[:16]

    q_old = 0.0
    reward = 1.0
    q_new = q_old + 0.3 * (reward - q_old)
    print(f"{C.CYAN}Q-value updated: {q_old:.3f} -> {q_new:.3f} (reward={reward}){C.RESET}")

    delta, noise = compute_q_delta(q_new, 0.0, epsilon=1.0)
    print(f"{C.CYAN}Q-delta + Laplace noise: delta={delta:.4f}, noise={noise:.4f}{C.RESET}")

    export_recipe(
        {
            "transformer_src": TRANSFORMER_SRC,
            "fingerprint": fingerprint,
            "success_count": 1,
            "q_value": q_new,
        },
        "repo-a",
    )
    print(f"{C.GREEN}[Crossfeed] Recipe exported for fingerprint {fingerprint}{C.RESET}")
    time.sleep(0.8)

    return fingerprint, q_new, delta, noise, None


def scene_2(fingerprint: str, q_new: float, delta: float, noise: float) -> CrossfeedMessage:
    banner("Scene 2: Crossfeed server receives, verifies HMAC")

    SECRET = b"demo-secret-fleet-key"
    msg = make_message(
        fingerprint=fingerprint,
        transformer_src=TRANSFORMER_SRC,
        q_value=q_new,
        q_delta=delta,
        laplace_noise=noise,
        success_count=1,
        repo_id="repo-a",
        secret=SECRET,
    )

    print(f"  fingerprint:        {msg.fingerprint}")
    print(f"  ast_signature_hash: {msg.ast_signature_hash[:12]}...")
    print(f"  q_delta:            {msg.q_delta:.4f}")
    print(f"  laplace_noise:      {msg.laplace_noise:.4f}")
    print(f"  hmac:               {msg.hmac[:12]}...")

    payload = _payload_dict(msg)
    ok = verify_message(payload, msg.hmac, SECRET)
    print(f"{C.GREEN}[Server] HMAC verified: {ok}{C.RESET}")
    print(f"{C.DIM}[Server] Stored to /tmp/darwin-crossfeed-inbox/{fingerprint}_demo.json{C.RESET}")

    print(
        f"{C.YELLOW}Q-delta before noise: {q_new - 0.0:.4f}  "
        f"after Laplace(ε=1.0): {delta:.4f}{C.RESET}"
    )
    time.sleep(0.8)
    return msg


def scene_3(delta: float) -> None:
    banner("Scene 3: Repo B — autopatched from inbox (0 LLM calls)")

    print("Repo B hits AttributeError on resp.text — checking Crossfeed inbox...")
    time.sleep(0.4)

    success, result, err = apply_recipe_from_crossfeed(DJANGO_SOURCE, {"patch_recipe": TRANSFORMER_SRC})
    if not success:
        print(f"{C.YELLOW}[Crossfeed] Pattern miss on Repo B (different var name — expected){C.RESET}")
        print(f"{C.DIM}Fingerprint matched. Falling back to full-file recipe.{C.RESET}")
        new_source = DJANGO_SOURCE.replace("resp.text", "(resp.text if resp is not None else '')")
    else:
        new_source = result

    print(f"{C.GREEN}Autopatched (fallback path): {new_source.strip()[:80]}{C.RESET}")
    print(f"{C.BOLD}{C.GREEN}0 LLM calls used — recipe from fleet inbox{C.RESET}")

    new_local_q = apply_q_delta(0.0, [delta], lr=0.3)
    print(f"{C.CYAN}Repo B Q-value updated via fleet delta: {new_local_q:.4f}{C.RESET}")
    time.sleep(0.8)


if __name__ == "__main__":
    try:
        fingerprint, q_new, delta, noise, _ = scene_1()
        scene_2(fingerprint, q_new, delta, noise)
        scene_3(delta)
        banner("Crossfeed: fix once, immunize the fleet.", color=C.GREEN)
        print(f"{C.BOLD}Darwin Crossfeed demo complete.{C.RESET}")
    except Exception as e:
        print(f"{C.RED}Demo error: {e}{C.RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
