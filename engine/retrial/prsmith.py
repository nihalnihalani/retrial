"""PRSmith: turn a verdict into a real GitHub PR with the evidence dossier.

The demo cannot dead-end. A FIXED verdict opens a PR adding the winning patched
seed under seeds/fixed/<test_name> (the ORIGINAL flaky seed is the demo fixture
and is never overwritten). A QUARANTINE verdict opens a PR adding a quarantine
dossier under seeds/quarantine/<test_name>.md explaining that no fix survived.
Either way the PR body is the governance receipt: flake rates + Wilson CIs,
cause class + explanation, trials, and the Braintrust permalinks.

All GitHub writes go through `gh api` (already authed) — server-side ref/blob/PR
creation, so we NEVER touch the local working tree or its branch. Emits
`pr_opened {url}`. Any failure degrades to None; opening a PR must never crash a
run.
"""
import base64
import json
import re
import subprocess
import uuid

from .settings import get_settings

_DEFAULT_REPO = "nihalnihalani/retrial"


def _slug(name):
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-").lower() or "test"


def _detect_repo():
    repo = get_settings().retrial_repo
    if repo:
        return repo
    try:
        out = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner",
                              "-q", ".nameWithOwner"],
                             capture_output=True, text=True, timeout=15)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return _DEFAULT_REPO


def _pct(x):
    return "n/a" if x is None else f"{x:.0%}"


def _ci(ci):
    return "n/a" if not ci else f"{ci[0]:.0%}-{ci[1]:.0%}"


def _receipts(result):
    """'## Statistical receipts' — every number the verdict rests on, with
    honest omissions: a line renders ONLY when its datum exists; absent data
    says nothing (never 'n/a' dressed up as evidence, never a claim without a
    measurement). Pure and defensive: every access is a `.get` chain over the
    coordinator result shape, so a sparse or malformed dict yields fewer lines,
    never a raise."""
    verdict = result.get("verdict")
    detect = result.get("detect") or {}
    lines = ["## Statistical receipts", ""]

    # Before: the lie detector's own measurement on the unmodified test.
    orig = result.get("orig_flake_rate")
    if orig is not None:
        bits = [f"**Before (detect):** empirical flake rate **{_pct(orig)}**"]
        if detect.get("wilson_ci"):
            bits.append(f"95% CI {_ci(detect.get('wilson_ci'))}")
        trials = detect.get("trials")
        if trials is not None:
            tail = f"{trials} reruns"
            if detect.get("fails") is not None:
                tail += f", {detect.get('fails')} failures"
            bits.append(tail)
        lines.append("- " + " · ".join(bits))

    if verdict == "FIXED":
        w = result.get("winner") or {}
        conf = result.get("confirmation") or {}
        if w.get("flake_rate") is not None:
            bits = [f"**After (winner):** flake rate **{_pct(w.get('flake_rate'))}**"]
            if w.get("wilson_ci"):
                bits.append(f"95% CI {_ci(w.get('wilson_ci'))}")
            if w.get("trials") is not None:
                bits.append(f"{w.get('trials')} reruns")
            lines.append("- " + " · ".join(bits))
        if conf.get("flake_rate") is not None:
            bits = [f"**Confirmation:** flake rate **{_pct(conf.get('flake_rate'))}**"]
            if conf.get("wilson_ci"):
                bits.append(f"95% CI {_ci(conf.get('wilson_ci'))}")
            if conf.get("trials") is not None:
                bits.append(f"{conf.get('trials')} reruns")
            lines.append("- " + " · ".join(bits))
            lines.append("- Confirmation was an independent re-verify; the winner "
                         "stands only because it read STABLE.")
    elif verdict == "QUARANTINE":
        hyps = result.get("hypotheses") or []
        best = (min(hyps, key=lambda r: r.get("flake_rate", 1.0))
                if hyps else None)
        if best and best.get("flake_rate") is not None:
            bits = [f"**Best candidate:** flake rate **{_pct(best.get('flake_rate'))}**"]
            if best.get("wilson_ci"):
                bits.append(f"95% CI {_ci(best.get('wilson_ci'))}")
            if best.get("trials") is not None:
                bits.append(f"{best.get('trials')} reruns")
            lines.append("- " + " · ".join(bits))
        lines.append("- No candidate's CI cleared the threshold — quarantine "
                     "recommended.")

    # Braintrust permalinks WHEN present; an honest single line when absent.
    bt = result.get("braintrust") or {}
    bt_links = [(k, v) for k, v in bt.items() if v]
    if bt_links:
        lines.append("- Braintrust experiments: "
                     + " · ".join(f"[{k}]({v})" for k, v in bt_links))
    else:
        lines.append("- Braintrust ledger: not recorded for this run "
                     "(no BRAINTRUST_API_KEY).")

    lines += ["",
              "*Method: Wilson 95% score intervals over independent sandbox "
              "reruns; verdicts require the whole CI to clear the threshold, "
              "not the point estimate.*", ""]
    return lines


class PRSmith:
    """Opens a fix-or-quarantine PR from a tournament result via the gh CLI."""

    def __init__(self, repo=None, base="main", bus=None):
        self.repo = repo or _detect_repo()
        self.base = base
        self.bus = bus

    # -- gh api plumbing -------------------------------------------------
    def _gh(self, args, payload=None, want_json=True):
        """Run `gh api ...`; POST/PUT bodies are piped as JSON via stdin."""
        cmd = ["gh", "api"] + args
        inp = None
        if payload is not None:
            cmd += ["--input", "-"]
            inp = json.dumps(payload)
        out = subprocess.run(cmd, input=inp, capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise RuntimeError(f"gh api {args[:2]} failed: {out.stderr.strip()[:200]}")
        return json.loads(out.stdout) if want_json and out.stdout.strip() else out.stdout

    # -- dossier ---------------------------------------------------------
    def _dossier(self, result, test_name):
        verdict = result["verdict"]
        detect = result.get("detect", {})
        bt = result.get("braintrust", {}) or {}
        orig = result.get("orig_flake_rate")

        lines = [f"# Retrial verdict: **{verdict}** — `{test_name}`", ""]
        lines.append(f"Original flake rate: **{_pct(orig)}** "
                     f"(95% CI {_ci(detect.get('wilson_ci'))}, "
                     f"{detect.get('trials', '?')} reruns).")
        lines.append("")

        if verdict == "FIXED":
            w = result["winner"]
            conf = result.get("confirmation") or {}
            lines += [
                f"## Winning fix — {w.get('cause_class')}",
                "",
                f"{w.get('explanation') or ''}",
                "",
                f"- Tournament flake rate: **{_pct(w.get('flake_rate'))}** "
                f"(95% CI {_ci(w.get('wilson_ci'))}, {w.get('trials')} reruns)",
                f"- Confirmation round: **{_pct(conf.get('flake_rate'))}** "
                f"(95% CI {_ci(conf.get('wilson_ci'))}, {conf.get('trials')} reruns)",
                f"- Generated by model: `{w.get('model') or 'cached'}`",
            ]
        else:
            hyps = result.get("hypotheses", [])
            best = min(hyps, key=lambda r: r["flake_rate"]) if hyps else None
            lines += ["## No fix survived — quarantine recommended", ""]
            if best:
                lines.append(
                    f"Best candidate (`{best.get('cause_class')}`) still flaked at "
                    f"**{_pct(best.get('flake_rate'))}** "
                    f"(95% CI {_ci(best.get('wilson_ci'))}, {best.get('trials')} reruns) "
                    f"— above the decision threshold.")
            else:
                lines.append("No fix hypotheses were generated.")
            lines.append("")
            lines.append("Quarantine this test until a fix stabilizes it below threshold.")

        # Statistical receipts: the table of every number the verdict rests on
        # (the narrative lines above are the story; this is the evidence). Slight
        # duplication with the body is deliberate and honest.
        lines += ["", *_receipts(result)]

        # Braintrust receipts
        if bt:
            lines += ["", "## Braintrust receipts"]
            for series, url in bt.items():
                if url:
                    lines.append(f"- `{series}`: {url}")
        lines += ["", "---", "*Generated by Retrial — every flaky test deserves a retrial.*"]
        return "\n".join(lines)

    # -- public ----------------------------------------------------------
    def open_pr(self, result, test_name):
        """Open a PR for a FIXED or QUARANTINE result. Returns the PR URL or None."""
        try:
            verdict = result["verdict"]
            if verdict not in ("FIXED", "QUARANTINE"):
                return None
            short = uuid.uuid4().hex[:7]
            slug = _slug(test_name)
            body = self._dossier(result, test_name)

            if verdict == "FIXED":
                branch = f"retrial/fix-{slug}-{short}"
                # `slug`, not raw test_name: test_name used to be a Path.name
                # inside seeds/, but a pytest node id from a user-supplied repo
                # is now a legitimate value here — and it contains "/" and "::".
                # Interpolating it raw made
                # `seeds/fixed/../../.github/workflows/x.yml` a write primitive
                # against a repo-scoped GitHub token.
                path = f"seeds/fixed/{slug}.py"
                file_content = result["winner"]["patched_code"]
                title = (f"fix: {test_name} flaky test "
                         f"({result['winner'].get('cause_class')}, "
                         f"{_pct(result.get('orig_flake_rate'))} -> "
                         f"{_pct((result.get('confirmation') or {}).get('flake_rate'))})")
            else:
                branch = f"retrial/quarantine-{slug}-{short}"
                path = f"seeds/quarantine/{slug}.md"
                file_content = body
                title = f"quarantine: {test_name} flaky test (no fix survived)"

            # 1) base sha -> 2) branch ref -> 3) file blob -> 4) PR (all server-side)
            base_sha = self._gh(
                [f"repos/{self.repo}/git/ref/heads/{self.base}"])["object"]["sha"]
            self._gh([f"repos/{self.repo}/git/refs", "--method", "POST"],
                     payload={"ref": f"refs/heads/{branch}", "sha": base_sha})
            self._gh([f"repos/{self.repo}/contents/{path}", "--method", "PUT"],
                     payload={"message": title, "branch": branch,
                              "content": base64.b64encode(file_content.encode()).decode()})
            pr = self._gh([f"repos/{self.repo}/pulls", "--method", "POST"],
                          payload={"title": title, "body": body,
                                   "head": branch, "base": self.base})
            url = pr.get("html_url")
            if self.bus is not None and url:
                self.bus.emit("pr_opened", {"url": url, "verdict": verdict})
            return url
        except Exception:
            return None
