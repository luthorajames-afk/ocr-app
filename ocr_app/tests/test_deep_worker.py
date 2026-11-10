"""Tests du worker Approfondi réel (subprocess, style WorkerDriver).

Pilote le venv d'évaluation ``.venv-eval-advanced`` (Python + Docling, R1/
R13/R4) — exactement la commande résolue par ``resolve_deep_backend()`` :

    <venv>/Scripts/python.exe -m ocr_app.workers.worker_main
        --mode approfondi --input fact_001.pdf --page 1
        [--preset ink | --filter-json <json>] [--exclude "x0,y0,x1,y1;…"]
        --work-dir <dossier accentué>

Vérifications (mission) :

- ``--preset ink --exclude …`` : ``page_done`` réel Docling, page/chemin
  réels, et **aucun** bloc restant intersectant la zone à ≥ 50 % — la zone
  est dérivée d'un bloc réel du run Avancé de référence ;
- sans ``--preset/--filter-json/--exclude`` : parité exacte avec le mode
  Avancé (mêmes blocs, document original remis au moteur) ;
- ``--filter-json`` / ``--exclude`` / ``--preset`` hostiles → exit 2,
  stdout JSON pur (zéro ligne non protocole) ;
- annulation pendant la conversion (étape ``ocr``) → arrêt < 2 s (R2) ;
- dossiers de travail avec accents/espaces (R14).

Garde-fous : toute la famille est SKIPPED si le venv ou Docling est absent
(les tests restent verts sur une machine sans backend Avancé).

Tous les tests sont headless (aucun Qt). Budget : chaque run Docling réel
dure ~20-50 s (préchauffage + conversion) ; les timeouts sont dimensionnés
en conséquence.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKER_MODULE = "ocr_app.workers.worker_main"
FACT_001 = PROJECT_ROOT / "data" / "test_suite" / "facts" / "fact_001.pdf"

# Zone de référence (points PDF) : couvre le bloc « SARL Dupont & Fils » du
# run Avancé de référence (bbox ≈ 44.16,22.96 → 195.97,40.82). Le test la
# redérive dynamiquement des blocs de référence (robuste à la dérive Docling).
_EXCLUDE_ZONE_FALLBACK = (42.2, 21.0, 198.0, 42.8)

RUN_TIMEOUT_S = 240.0

# --- Garde-fous : backend Avancé disponible ? -----------------------------------

VENV_DIR = PROJECT_ROOT / ".venv-eval-advanced"
VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
if not VENV_PYTHON.is_file():
    VENV_PYTHON = VENV_DIR / "bin" / "python"

HAS_VENV = VENV_PYTHON.is_file() and FACT_001.is_file()
HAS_DOCLING = (VENV_DIR / "Lib" / "site-packages" / "docling" / "__init__.py").is_file()

requires_deep_backend = pytest.mark.skipif(
    not HAS_VENV or not HAS_DOCLING,
    reason="backend Avancé indisponible (venv .venv-eval-advanced / Docling absent)",
)


# --- Driver ----------------------------------------------------------------------


class DeepWorkerDriver:
    """Sous-processus worker_main réel + lecture asynchrone du protocole."""

    def __init__(
        self,
        work_dir: Path,
        *,
        mode: str = "approfondi",
        input_path: Optional[Path] = None,
        page: Optional[int] = 1,
        preset: Optional[str] = None,
        filter_json: Optional[str] = None,
        exclude: Optional[str] = None,
        python: Optional[Path] = None,
        close_stdin_immediately: bool = False,
    ) -> None:
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(python or VENV_PYTHON),
            "-u",
            "-m",
            WORKER_MODULE,
            "--mode",
            mode,
        ]
        if input_path is not None:
            cmd += ["--input", str(input_path)]
        if page is not None:
            cmd += ["--page", str(page)]
        if preset is not None:
            cmd += ["--preset", preset]
        if filter_json is not None:
            cmd += ["--filter-json", filter_json]
        if exclude is not None:
            cmd += ["--exclude", exclude]
        # R14 : le dossier de travail porte accents et espaces.
        cmd += ["--work-dir", str(self.work_dir)]

        env = dict(os.environ)
        env["PYTHONPATH"] = (
            str(PROJECT_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        )
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        self.received: list[dict] = []
        self.invalid_lines: list[str] = []
        self._stderr_lines: list[str] = []
        self._messages: "queue.Queue[object]" = queue.Queue()
        self._stdout_closed = False

        self._stdout_thread = threading.Thread(
            target=self._pump_stdout, name="deep-worker-stdout", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._pump_stderr, name="deep-worker-stderr", daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

        if close_stdin_immediately:
            # Erreurs d'usage : le worker doit répondre exit 2 sans attendre
            # la commande start (aucun traitement lancé).
            self.close_stdin()

    # --- Lecture ----------------------------------------------------------
    def _pump_stdout(self) -> None:
        assert self.proc.stdout is not None
        try:
            for raw in self.proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self.invalid_lines.append(line)
                    continue
                if not isinstance(message, dict):
                    self.invalid_lines.append(line)
                    continue
                self.received.append(message)
                self._messages.put(message)
        finally:
            self._messages.put(_EOF)

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        try:
            for raw in self.proc.stderr:
                self._stderr_lines.append(raw.rstrip("\r\n"))
        except (OSError, ValueError):
            pass

    def stderr_text(self) -> str:
        return "\n".join(self._stderr_lines)

    # --- Commandes --------------------------------------------------------
    def send(self, action: str, page: Optional[int] = None) -> None:
        assert self.proc.stdin is not None
        payload: dict = {"type": "command", "action": action}
        if page is not None:
            payload["page"] = page
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def send_raw(self, raw: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(raw + "\n")
        self.proc.stdin.flush()

    def close_stdin(self) -> None:
        if self.proc.stdin is not None:
            try:
                self.proc.stdin.close()
            except (OSError, ValueError):
                pass

    # --- Messages ---------------------------------------------------------
    def next_message(self, timeout: float) -> Optional[dict]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                item = self._messages.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            if item is _EOF:
                self._stdout_closed = True
                self._messages.put(_EOF)
                return None
            assert isinstance(item, dict)
            return item

    def wait_for(self, predicate, timeout: float, description: str) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"{description} non reçu en {timeout:.1f} s ; "
                    f"messages reçus : {self.received!r} ; "
                    f"stderr : {self.stderr_text()!r}"
                )
            message = self.next_message(min(remaining, 0.5))
            if message is None:
                if self._stdout_closed:
                    raise AssertionError(
                        f"le worker s'est terminé avant {description} ; "
                        f"code={self.proc.poll()} ; messages reçus : "
                        f"{self.received!r} ; stderr : {self.stderr_text()!r}"
                    )
                continue
            if predicate(message):
                return message

    def wait_exit(self, timeout: float) -> int:
        return self.proc.wait(timeout=timeout)

    # --- Arrêt ------------------------------------------------------------
    def close(self) -> None:
        if self.proc.poll() is None:
            self.close_stdin()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        self._stdout_thread.join(timeout=0.5)
        self._stderr_thread.join(timeout=0.5)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass


_EOF = object()


@pytest.fixture
def driver_factory(tmp_path: Path):
    drivers: list[DeepWorkerDriver] = []

    def make(**kwargs) -> DeepWorkerDriver:
        # R14 : dossier de travail accentué sous %TEMP%.
        work_dir = tmp_path / "aocri deep éàç"
        driver = DeepWorkerDriver(work_dir, **kwargs)
        drivers.append(driver)
        return driver

    yield make

    for driver in drivers:
        driver.close()


def _run_real_page(
    driver_factory, work_subdir: str = "", **kwargs
) -> tuple[DeepWorkerDriver, dict]:
    """Lance le worker réel sur fact_001.pdf page 1 jusqu'à page_done."""
    driver = driver_factory(**kwargs)
    driver.send("start")
    done = driver.wait_for(
        lambda m: m.get("type") == "page_done" and m.get("page") == 1,
        timeout=RUN_TIMEOUT_S,
        description="page_done (run Docling réel)",
    )
    return driver, done


def _load_blocks(work_dir: Path) -> list[dict]:
    path = work_dir / "page_001.json"
    assert path.is_file(), f"résultat manquant : {path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["number"] == 1
    return data["blocks"]


def _overlap_ratio(bbox, zone) -> float:
    """Part de la surface du bloc couverte par la zone (cf. pipeline)."""
    x0, y0, x1, y1 = bbox
    zx0, zy0, zx1, zy1 = zone
    width, height = x1 - x0, y1 - y0
    if width <= 0 or height <= 0:
        return 0.0
    overlap_w = min(x1, zx1) - max(x0, zx0)
    overlap_h = min(y1, zy1) - max(y0, zy0)
    if overlap_w <= 0 or overlap_h <= 0:
        return 0.0
    return (overlap_w * overlap_h) / (width * height)


def _zone_for_block(bbox) -> str:
    """Zone couvrant le cœur du bloc (≥ 50 % de sa surface, marge interne)."""
    x0, y0, x1, y1 = bbox
    dx, dy = (x1 - x0) * 0.15, (y1 - y0) * 0.15
    return f"{x0 + dx:.2f},{y0 + dy:.2f},{x1 - dx:.2f},{y1 - dy:.2f}"


# --- Run de référence (mode Avancé, une seule exécution par session) ------------

_REFERENCE: dict = {}


@pytest.fixture(scope="module")
def reference_run(tmp_path_factory):
    """Run Avancé (document original) : blocs de référence, exécuté une fois."""
    if "blocks" not in _REFERENCE:
        work = tmp_path_factory.mktemp("ref deep éàç") / "out"
        driver = DeepWorkerDriver(
            work, mode="advanced", input_path=FACT_001, page=1
        )
        try:
            driver.send("start")
            driver.wait_for(
                lambda m: m.get("type") == "page_done" and m.get("page") == 1,
                timeout=RUN_TIMEOUT_S,
                description="page_done du run de référence Avancé",
            )
            assert driver.wait_exit(timeout=30.0) == 0
            assert driver.invalid_lines == [], "stdout du run de référence : JSON pur"
            _REFERENCE["blocks"] = _load_blocks(work)
        finally:
            driver.close()
    return _REFERENCE["blocks"]


# --- Tests -----------------------------------------------------------------------


@requires_deep_backend
class TestDeepWorkerReal:
    def test_reference_run_produces_real_blocks(self, reference_run) -> None:
        assert len(reference_run) > 0, "Docling doit produire des blocs réels"
        assert any(block["text"].strip() for block in reference_run)

    def test_approfondi_without_params_matches_advanced_parity(
        self, driver_factory, reference_run
    ) -> None:
        """Sans --preset/--filter-json/--exclude : parité exacte avec Avancé."""
        driver, done = _run_real_page(
            driver_factory, mode="approfondi", input_path=FACT_001
        )
        try:
            assert driver.wait_exit(timeout=30.0) == 0
            assert driver.invalid_lines == [], "stdout : JSON pur uniquement"
            assert Path(done["result_path"]).parent == driver.work_dir.resolve(), (
                "R14 : résultat écrit dans le dossier de travail accentué"
            )
            blocks = _load_blocks(driver.work_dir)
            assert [b["text"] for b in blocks] == [
                b["text"] for b in reference_run
            ], "parité Avancé : mêmes blocs (document original remis au moteur)"
            for got, expected in zip(blocks, reference_run):
                assert got["bbox"] == pytest.approx(expected["bbox"], abs=0.01)
        finally:
            driver.close()

    def test_approfondi_preset_and_exclude_prune_blocks(
        self, driver_factory, reference_run
    ) -> None:
        """--preset ink --exclude <zone> : blocs ≥ 50 % intersectés retirés."""
        target = next(
            (b for b in reference_run if b["text"].strip()), None
        )
        assert target is not None, "un bloc réel est requis"
        zone = _zone_for_block(target["bbox"])
        driver, done = _run_real_page(
            driver_factory,
            mode="approfondi",
            preset="ink",
            exclude=zone,
        )
        try:
            assert driver.wait_exit(timeout=30.0) == 0
            assert driver.invalid_lines == [], "stdout : JSON pur uniquement"
            assert Path(done["result_path"]).parent == driver.work_dir.resolve()

            zone_tuple = tuple(float(v) for v in zone.split(","))
            blocks = _load_blocks(driver.work_dir)
            assert len(blocks) > 0
            # Aucun bloc restant ne doit intersecter la zone à ≥ 50 %.
            for block in blocks:
                ratio = _overlap_ratio(block["bbox"], zone_tuple)
                assert ratio < 0.5, (
                    f"bloc {block['text'][:40]!r} encore couvert à "
                    f"{ratio:.0%} par la zone d'exclusion"
                )
            # Le bloc visé (couvert à ~72 % par la zone cœur) est parti.
            texts = [b["text"] for b in blocks]
            assert target["text"] not in texts, (
                f"le bloc ciblé {target['text'][:40]!r} doit être retiré"
            )
        finally:
            driver.close()

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"filter_json": "{bad"},
            {"filter_json": '["pas un objet"]'},
            {"preset": "bogus"},
            {"exclude": "1,2,3"},
            {"exclude": "abc,1,2,3"},
            {"exclude": "nan,0,100,100"},
            {"exclude": "0,0,100,inf"},
        ],
        ids=[
            "filter-json-illisible",
            "filter-json-non-objet",
            "preset-inconnu",
            "exclude-3-coords",
            "exclude-texte",
            "exclude-nan",
            "exclude-inf",
        ],
    )
    def test_hostile_args_exit_2_with_pure_stdout(
        self, driver_factory, kwargs
    ) -> None:
        """Args hostiles → exit 2, stdout JSON pur (zéro ligne), stderr explicite."""
        driver = driver_factory(close_stdin_immediately=True, **kwargs)
        code = driver.wait_exit(timeout=60.0)
        assert code == 2, f"code de sortie attendu 2, reçu {code}"
        assert driver.invalid_lines == []
        assert driver.received == [], (
            f"aucun message de protocole attendu, reçu : {driver.received!r}"
        )
        stderr_low = driver.stderr_text().lower()
        assert (
            "invalide" in stderr_low
            or "inconnu" in stderr_low
            or "preset" in stderr_low
            or "exclude" in stderr_low
        ), "un message d'usage lisible doit aller sur stderr"
        driver.close()

    def test_hostile_filter_json_values_are_clamped_not_rejected(
        self, driver_factory
    ) -> None:
        """``--filter-json`` avec valeurs absurdes : clamp normal (exit 0)."""
        driver, done = _run_real_page(
            driver_factory,
            mode="approfondi",
            filter_json=json.dumps({"contrast": 999, "brightness": "x"}),
        )
        try:
            assert driver.wait_exit(timeout=30.0) == 0
            blocks = _load_blocks(driver.work_dir)
            assert blocks, "clamp des filtres : la page est traitée normalement"
        finally:
            driver.close()

    def test_cancel_during_conversion_under_2_seconds(self, driver_factory) -> None:
        """Cancel à l'étape ``ocr`` (conversion) : arrêt < 2 s (R2)."""
        driver = driver_factory(mode="approfondi")
        try:
            driver.send("start")
            driver.wait_for(
                lambda m: m.get("type") == "progress" and m.get("stage") == "ocr",
                timeout=RUN_TIMEOUT_S,
                description="progress étape ocr (conversion Docling)",
            )
            started = time.perf_counter()
            driver.send("cancel")
            code = driver.proc.wait(timeout=10.0)
            elapsed = time.perf_counter() - started
            print(f"\n[perf] cancel pendant conversion : {elapsed:.2f} s")
            assert code == 0, "annulation propre (code 0)"
            assert elapsed < 2.0, f"annulation trop lente : {elapsed:.2f} s >= 2 s"
        finally:
            driver.close()

    def test_simulated_approfondi_without_input(self, driver_factory) -> None:
        """Sans --input : pages placeholder, protocole complet, texte dédié."""
        driver = driver_factory(mode="approfondi", input_path=None, page=None)
        try:
            driver.send("start")
            done = driver.wait_for(
                lambda m: m.get("type") == "page_done" and m.get("page") == 1,
                timeout=60.0,
                description="page_done simulé approfondi",
            )
            assert driver.wait_exit(timeout=15.0) == 0
            assert driver.invalid_lines == []
            data = json.loads(Path(done["result_path"]).read_text(encoding="utf-8"))
            assert "Approfondi" in data["blocks"][0]["text"]
            assert "éèàç" in data["blocks"][0]["text"], "les accents survivent (R14)"
        finally:
            driver.close()
