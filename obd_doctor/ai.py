"""AI explanations of scan results, using Anthropic's Claude API.

The scan data (trouble codes, freeze frame, live sensor readings, readiness
monitors and the rule-based findings) is turned into a compact text summary
and sent to Claude, which explains it in plain language for the car owner.
The VIN is never sent. Follow-up questions continue the same conversation.

API key lookup order: ANTHROPIC_API_KEY environment variable, then the key
saved in OBD EFT's settings (see config.py).
"""

from __future__ import annotations

import os

from . import config, decoder
from .diagnose import code_details

MODEL = "claude-opus-5-5"
EFFORT = "medium"
MAX_TOKENS = 64000
# Server-side fallback: if the model declines a request, the API retries it on a
# suitable fallback model within the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You are an experienced, honest automotive diagnostic technician helping a car owner \
understand a scan of their car's OBD-II system. The owner is usually not a mechanic.

You receive: trouble codes (with the module that reported them and whether they are stored, pending or \
permanent), freeze-frame data, live sensor readings, emissions readiness monitors, and findings from a \
simple rule-based checker. Treat the rule-based findings as hints, not facts. The owner may also describe \
the vehicle and the symptoms they notice; use both when they are given.

For the first explanation, use exactly these sections, as markdown "## " headings:
## In plain English
Two or three sentences on what is going on.
## How urgent is it?
Start with one of: "Safe to drive", "Drive gently and get it fixed soon", or "Stop driving". Then explain why.
## Most likely cause
Rank the likely causes and say what in the data points to each. Connect related codes and sensor readings \
into one story where they fit together, and say so when they don't.
## What to check, in order
Cheapest and easiest first. Mark each step as DIY or Shop, and give rough cost ranges, clearly labelled as \
estimates that vary by vehicle and region.
## What to tell the mechanic
A short note the owner can show at a repair shop.

Rules:
- Be concrete: cite the codes and the actual numbers from the data.
- Say "Stop driving" for overheating, low oil pressure, a flashing or severe misfire, or a fault that \
makes braking or steering unsafe. An ABS fault alone leaves normal brakes working (say so, and advise extra \
stopping distance). For airbag faults, explain that airbags may not deploy, and that airbag repairs belong \
with a professional.
- Manufacturer-specific codes (P1xxx, B1xxx, U1xxx and similar) mean different things on different brands. \
Unless the vehicle is known and you are confident, say that the meaning depends on the make.
- If the data is not enough to be sure, say what extra test or information would settle it.
- Never recommend clearing codes to pass an inspection, or disabling emissions equipment.
- Plain language. Explain any technical term the first time you use it. About 350-550 words.
- Formatting: only "## " headings, "- " bullets, numbered lists and **bold**. No tables, no code blocks.

For follow-up questions, answer directly and concisely in the same plain style, without repeating the full structure."""


class AIError(Exception):
    """A problem the user should see, with a friendly message."""


# --------------------------------------------------------------------------- data -> prompt
def _fmt(pid, value) -> str:
    name, unit, _ = decoder.PIDS[pid]
    return f"{name}: {value} {unit}".strip()


def build_scan_summary(scan: dict, findings, vehicle: str = "", symptoms: str = "") -> str:
    """Compact, model-friendly text version of a scan. The VIN is left out on purpose."""
    lines = []
    from . import vin as vinlib
    from .diagnose import inspection_readiness
    lines.append(f"Vehicle (as described by the owner): {vehicle.strip() or 'not given'}")
    decoded = vinlib.describe(scan.get("vin"))
    if decoded:
        lines.append(f"Vehicle decoded from the VIN (make, model year, country only): {decoded}")
    lines.append(f"Symptoms the owner notices: {symptoms.strip() or 'none given'}")
    lines.append(f"OBD protocol: {scan.get('protocol')}")

    st = scan.get("status") or {}
    if st:
        lines.append(f"Check engine light: {'ON' if st['mil_on'] else 'OFF'}; engine type: {st['engine_type']}; "
                     f"codes reported by the engine computer: {st['dtc_count']}")
        if st.get("monitors"):
            lines.append("Readiness monitors: " + ", ".join(f"{k} {v}" for k, v in st["monitors"].items()))
        insp = inspection_readiness(scan)
        if insp:
            lines.append("Emissions inspection (US EPA guideline): " + (
                "likely PASS" if insp["pass"] else "likely FAIL because " + "; ".join(insp["reasons"])))

    lines.append("\nTROUBLE CODES")
    details = code_details(scan)
    if not details:
        lines.append("- none stored, pending or permanent")
    for d in details:
        tag = " (manufacturer-specific)" if d["manufacturer_specific"] else ""
        lines.append(f"- {d['code']}: {d['description']}{tag}; found in {', '.join(d['found_in'])}")

    ff = scan.get("freeze_frame")
    if ff:
        lines.append(f"\nFREEZE FRAME (conditions when {ff['trigger_dtc']} was set)")
        lines += [f"- {_fmt(p, v)}" for p, v in ff["data"].items()]

    live = scan.get("live") or {}
    if live:
        rpm = live.get(0x0C)
        state = "engine running" if rpm and rpm > 400 else "engine off or not reported"
        lines.append(f"\nLIVE DATA at the time of the scan ({state})")
        lines += [f"- {_fmt(p, v)}" for p, v in live.items() if p != 0x01]
    if scan.get("adapter_voltage"):
        lines.append(f"- Voltage at the OBD port: {scan['adapter_voltage']:.1f} V")

    mods = scan.get("modules") or {}
    if mods:
        lines.append("\nABS / AIRBAG MODULES (read with manufacturer-level UDS diagnostics)")
        if not mods.get("supported"):
            lines.append(f"- not readable on this car: {mods.get('note')}")
        elif not mods.get("list"):
            lines.append("- no ABS or airbag module answered at the common addresses checked")
        for m in mods.get("list", []):
            codes = ", ".join(f"{d['code']} ({'/'.join(d['status']) or 'stored'})" for d in m["dtcs"]) or "no codes"
            lines.append(f"- {m['label']}: {codes}")

    hist = scan.get("history")
    if hist:
        lines.append(f"\nCHANGES SINCE THE LAST SCAN OF THIS VEHICLE ({hist['previous_time']}, "
                     f"{hist['scans_before']} earlier scan(s) on record)")
        for key, text in (("new", "new"), ("returned", "came back after being gone"),
                          ("resolved", "gone"), ("persisting", "still present")):
            lines.append(f"- {text}: {', '.join(hist[key]) or 'none'}")

    lines.append("\nRULE-BASED FINDINGS (heuristic hints)")
    if not findings:
        lines.append("- none")
    for f in findings:
        extra = f"; possible causes: {'; '.join(f.causes)}" if f.causes else ""
        codes = f" [{', '.join(f.codes)}]" if f.codes else ""
        lines.append(f"- {f.severity.upper()}: {f.title}{codes}{extra}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- API key
def get_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or config.get("anthropic_api_key")


def has_api_key() -> bool:
    return bool(get_api_key())


# --------------------------------------------------------------------------- explainer
class AIExplainer:
    """Holds one conversation: an explanation of a scan plus follow-up questions."""

    def __init__(self, client=None):
        self._client = client
        self.messages: list = []
        self.transcript: list[tuple[str, str]] = []  # (role, text) for reports

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise AIError("The 'anthropic' package is missing. Install it with: pip install anthropic") from exc
            key = get_api_key()
            if not key:
                raise AIError("No Anthropic API key is set. Add one in AI settings "
                              "(get a key at console.anthropic.com).")
            self._client = anthropic.Anthropic(api_key=key, max_retries=3)
        return self._client

    def explain(self, scan, findings, vehicle="", symptoms="", on_text=None) -> str:
        summary = build_scan_summary(scan, findings, vehicle, symptoms)
        self.messages = [{"role": "user", "content":
                          "Here is the scan of my car. Please explain what is wrong and what I should do.\n\n"
                          + summary}]
        self.transcript = []
        try:
            return self._run(on_text)
        except Exception:
            self.messages = []
            raise

    def ask(self, question: str, on_text=None) -> str:
        if not self.messages:
            raise AIError("Run an AI explanation first, then ask follow-up questions.")
        self.messages.append({"role": "user", "content": question})
        self.transcript.append(("user", question))
        try:
            return self._run(on_text)
        except Exception:
            self.messages.pop()  # keep the conversation valid for the next try
            self.transcript.pop()
            raise

    def _run(self, on_text) -> str:
        import anthropic

        on_text = on_text or (lambda _t: None)
        try:
            with self.client.beta.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=self.messages,
                output_config={"effort": EFFORT},
                cache_control={"type": "ephemeral"},  # cheaper follow-up questions
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                for text in stream.text_stream:
                    on_text(text)
                final = stream.get_final_message()
        except AIError:
            raise
        except anthropic.AuthenticationError as exc:
            raise AIError("The Anthropic API key was rejected. Check it in AI settings.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise AIError("This API key doesn't have access to the model. Check your Anthropic account.") from exc
        except anthropic.RateLimitError as exc:
            raise AIError("Too many requests right now (rate limit). Wait a minute and try again.") from exc
        except anthropic.APIConnectionError as exc:
            raise AIError("Couldn't reach the AI service. Check your internet connection.\n"
                          "(Wi-Fi OBD adapters often take over your Wi-Fi - reconnect to the internet first.)") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code == 529:
                raise AIError("The AI service is busy or having problems. Try again in a few minutes.") from exc
            raise AIError(f"The AI service returned an error ({exc.status_code}): {exc.message}") from exc

        if final.stop_reason == "refusal":
            raise AIError("The AI declined to answer this request.")
        # Keep the full content (including thinking blocks) so follow-ups continue correctly
        self.messages.append({"role": "assistant", "content": final.content})
        text = "".join(b.text for b in final.content if b.type == "text")
        if final.stop_reason == "max_tokens":
            note = "\n\n(The answer was cut off because it reached the length limit.)"
            on_text(note)
            text += note
        self.transcript.append(("assistant", text))
        return text

    def transcript_text_for_view(self) -> str:
        """Transcript in the format the GUI renders (questions marked with ❓)."""
        return "\n\n".join(f"\u2753 {t}" if r == "user" else t for r, t in self.transcript)

    def transcript_text(self) -> str:
        parts = []
        for role, text in self.transcript:
            parts.append(f"Q: {text}" if role == "user" else text)
        return "\n\n".join(parts)
