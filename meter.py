#!/usr/bin/env python3
"""Progress, time and cost of one run: a percentage bar with elapsed and remaining
time, what the run cost in dollars (Jev, Serper, generative model) and how many
Serper credits it spent.

Every stage of a pipeline announces itself (`METER.stage("riassunti", total=48)`),
then ticks as items complete (`METER.tick()`). The percentage weighs stages by how
long they took on the previous runs (kept in meter-history.json, so the estimate
improves by itself); the first run uses rough defaults. A status line is printed at
every stage change and every 20 seconds, as a plain line: it stays readable in a log
file and in a monitor.

Costs. Jev bills input tokens only (0.042 dollars per million at the time of writing:
check console.typesafe.ai). Serper bills credits (about 0.001 dollars each on the
50k plan, an assumption). The generative models run on flat plans, so their tokens
are counted and priced only if the environment says so. Override with
JEV_USD_PER_MTOK_IN, SERPER_USD_PER_CREDIT, LLM_USD_PER_MTOK_IN, LLM_USD_PER_MTOK_OUT.
"""

import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(HERE, "meter-history.json")
JEV_IN = float(os.environ.get("JEV_USD_PER_MTOK_IN", "0.042"))
SERPER = float(os.environ.get("SERPER_USD_PER_CREDIT", "0.001"))
LLM_IN = float(os.environ.get("LLM_USD_PER_MTOK_IN", "0"))
LLM_OUT = float(os.environ.get("LLM_USD_PER_MTOK_OUT", "0"))


def clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class Meter:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.name = ""
        self.t0 = None
        self.stages = {}            # name -> expected seconds
        self.order = []
        self.done = {}              # name -> real seconds
        self.cur = None
        self.cur_t0 = 0.0
        self.total = 0
        self.count = 0
        self.jev_in = self.jev_out = self.jev_calls = 0
        self.jev_by = {}            # stage -> [requests, input tokens]
        self.llm_in = self.llm_out = self.llm_calls = 0
        self.serper_credits = 0
        self.serper_kinds = {}
        self._stop = None

    # ------------------------------------------------------------ run ------
    def start(self, name: str, stages: dict, ticker: bool = True) -> None:
        """stages: {stage name: default expected seconds}, in order."""
        self.reset()
        self.name = name
        self.t0 = time.time()
        try:
            hist = json.load(open(HISTORY)).get(name, {})
        except (OSError, ValueError):
            hist = {}
        self.order = list(stages)
        self.stages = {k: max(1.0, float(hist.get(k, v))) for k, v in stages.items()}
        if ticker:
            self._stop = threading.Event()
            threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while not self._stop.wait(20):
            self.report()

    def stage(self, name: str, total: int = 0) -> None:
        with self.lock:
            self._close()
            if name not in self.stages:                    # unplanned stage: tiny weight
                self.stages[name] = 1.0
                self.order.append(name)
            self.cur, self.cur_t0, self.total, self.count = name, time.time(), total, 0
        self.report()

    def _close(self) -> None:
        if self.cur:
            self.done[self.cur] = self.done.get(self.cur, 0) + time.time() - self.cur_t0
            self.cur = None

    def set_total(self, n: int) -> None:
        with self.lock:
            self.total, self.count = n, 0

    def tick(self, n: int = 1) -> None:
        with self.lock:
            self.count += n

    # --------------------------------------------------------- costs -------
    def jev(self, usage) -> None:
        with self.lock:
            self.jev_calls += 1
            row = self.jev_by.setdefault(self.cur or "-", [0, 0])
            row[0] += 1
            if usage:
                row[1] += usage.get("input_tokens", 0)
                self.jev_in += usage.get("input_tokens", 0)
                self.jev_out += usage.get("output_tokens", 0)

    def llm(self, usage) -> None:
        with self.lock:
            self.llm_calls += 1
            if usage:
                self.llm_in += usage.get("prompt_tokens", usage.get("input_tokens", 0))
                self.llm_out += usage.get("completion_tokens", usage.get("output_tokens", 0))

    def serper(self, credits: int = 1, kind: str = "ricerche") -> None:
        with self.lock:
            self.serper_credits += credits
            self.serper_kinds[kind] = self.serper_kinds.get(kind, 0) + credits

    # ------------------------------------------------------ numbers --------
    def usd(self) -> dict:
        jev = self.jev_in / 1e6 * JEV_IN
        serper = self.serper_credits * SERPER
        llm = self.llm_in / 1e6 * LLM_IN + self.llm_out / 1e6 * LLM_OUT
        return {"jev": jev, "serper": serper, "llm": llm, "total": jev + serper + llm}

    def progress(self) -> tuple:
        """(fraction 0-1, elapsed seconds, estimated seconds left)."""
        if self.t0 is None:
            return 0.0, 0.0, 0.0
        elapsed = time.time() - self.t0
        total_e = sum(self.stages.values())
        done_e = sum(self.stages[k] for k in self.done if k in self.stages and k != self.cur)
        cur_e = 0.0
        if self.cur:
            e = self.stages.get(self.cur, 1.0)
            if self.total:
                frac = min(self.count / self.total, 1.0)
            else:
                frac = min((time.time() - self.cur_t0) / e, 0.95)
            cur_e = e * frac
        frac_all = min((done_e + cur_e) / total_e, 0.999) if total_e else 0.0
        speed = (elapsed / (done_e + cur_e)) if (done_e + cur_e) > 5 else 1.0   # real vs expected
        speed = min(max(speed, 0.3), 5.0)
        left = max(0.0, (total_e - done_e - cur_e) * speed)
        return frac_all, elapsed, left

    def line(self) -> str:
        with self.lock:
            frac, elapsed, left = self.progress()
            n = int(round(frac * 20))
            bar = "#" * n + "-" * (20 - n)
            c = self.usd()
            where = f"{self.cur}" + (f" {self.count}/{self.total}" if self.total else "")
            return (f"  PROGRESSO [{bar}] {frac:4.0%} | {clock(elapsed)} trascorsi, ~{clock(left)} mancanti"
                    f" | {where} | Jev ${c['jev']:.4f} ({self.jev_in // 1000}k tok)"
                    f" | Serper {self.serper_credits} crediti (~${c['serper']:.3f})"
                    f" | LLM {(self.llm_in + self.llm_out) // 1000}k tok | totale ~${c['total']:.3f}")

    def report(self) -> None:
        if self.t0 is not None:
            print(self.line(), flush=True)

    def summary(self) -> dict:
        """What the page footer needs: seconds, dollars, credits."""
        with self.lock:
            c = self.usd()
            return {"seconds": int(time.time() - self.t0) if self.t0 else 0, "jev": c["jev"],
                    "serper_credits": self.serper_credits, "serper_usd": c["serper"],
                    "llm_tokens": self.llm_in + self.llm_out, "total": c["total"]}

    def finish(self) -> None:
        """Final line, cost table, and the stage durations saved for the next estimate."""
        if self._stop:
            self._stop.set()
        with self.lock:
            self._close()
            try:
                hist = json.load(open(HISTORY))
            except (OSError, ValueError):
                hist = {}
            elapsed = time.time() - self.t0 if self.t0 else 0
            old = hist.get(self.name, {})
            if elapsed >= 60:      # a run served from cache would skew the next estimate: skip it
                hist[self.name] = {k: round(0.5 * old[k] + 0.5 * v, 1) if k in old else round(v, 1)
                                   for k, v in self.done.items() if v >= 0.5}
                try:
                    json.dump(hist, open(HISTORY, "w"), indent=1)
                except OSError:
                    pass
            c = self.usd()
        print(f"\n  FINITO in {clock(elapsed)} | Jev {self.jev_calls} richieste, "
              f"{self.jev_in:,} token in -> ${c['jev']:.4f} | Serper {self.serper_credits} crediti "
              f"{dict(self.serper_kinds) or ''} (~${c['serper']:.3f}) | LLM {self.llm_calls} richieste, "
              f"{self.llm_in + self.llm_out:,} token (${c['llm']:.4f}) | totale ~${c['total']:.3f}", flush=True)
        if self.jev_by:
            parts = ", ".join(f"{k} {n} ric/{t // 1000}k tok (${t / 1e6 * JEV_IN:.4f})"
                              for k, (n, t) in sorted(self.jev_by.items(), key=lambda kv: -kv[1][1]))
            print(f"  Jev per fase: {parts}", flush=True)


METER = Meter()
