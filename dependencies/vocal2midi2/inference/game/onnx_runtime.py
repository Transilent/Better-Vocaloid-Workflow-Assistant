import json
from pathlib import Path

import numpy as np
import onnxruntime as ort

from inference.device_utils import resolve_onnx_providers


class GameOnnxModel:
    def __init__(self, model_dir: Path, *, requested_device: str | None = None):
        self.model_dir = Path(model_dir)
        config_path = self.model_dir / "config.json"
        if not config_path.exists():
            raise FileNotFoundError(f"GAME ONNX config not found: {config_path}")

        with config_path.open("r", encoding="utf-8") as f:
            self.config = json.load(f)

        self.samplerate = int(self.config["samplerate"])
        self.timestep = float(self.config["timestep"])
        self.loop = bool(self.config.get("loop", True))
        self.languages = dict(self.config.get("languages") or {})
        self.requested_device = str(requested_device)
        self.provider_name, self._sessions = self._load_sessions(self.requested_device)
        self._use_webgpu_iobinding = self.provider_name == "webgpu"
        active_ep = (
            self._sessions["encoder"].get_providers()[0]
            if self._sessions.get("encoder") and self._sessions["encoder"].get_providers()
            else "CPUExecutionProvider"
        )
        iob_tag = " + GPU IOBinding" if self._use_webgpu_iobinding else ""
        print(f"[VWA 后端] 音符提取 (GAME): {active_ep}{iob_tag}", flush=True)

    @staticmethod
    def _resolve_providers(device: str) -> tuple[str, list[str]]:
        return resolve_onnx_providers(device, label="GAME ONNX")

    def _load_sessions(self, device: str) -> tuple[str, dict[str, ort.InferenceSession]]:
        from inference.device_utils import configure_onnx_threads, create_onnx_session

        filenames = {
            "encoder": "encoder.onnx",
            "segmenter": "segmenter.onnx",
            "estimator": "estimator.onnx",
            "dur2bd": "dur2bd.onnx",
            "bd2dur": "bd2dur.onnx",
        }
        sessions = {}
        provider_name = "cpu"
        for key, filename in filenames.items():
            path = self.model_dir / filename
            if not path.exists():
                raise FileNotFoundError(f"Required GAME ONNX file not found: {path}")
            sess_options = ort.SessionOptions()
            configure_onnx_threads(sess_options)
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            sess_options.enable_mem_pattern = False
            sess_options.enable_cpu_mem_arena = False
            provider_name, sessions[key] = create_onnx_session(path, sess_options, device, label="GAME ONNX")
        return provider_name, sessions

    def shutdown(self) -> None:
        self._sessions.clear()

    release = shutdown

    def _infer_batch_webgpu_iobinding(
        self,
        *,
        waveforms: np.ndarray,
        durations: np.ndarray,
        known_durations: np.ndarray | None,
        boundary_threshold: float,
        boundary_radius: int,
        score_threshold: float,
        language: int,
        ts: list[float],
    ) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
        batch_size = int(waveforms.shape[0])
        enc_sess = self._sessions["encoder"]
        enc_iob = enc_sess.io_binding()
        enc_iob.bind_cpu_input("waveform", np.ascontiguousarray(waveforms, dtype=np.float32))
        enc_iob.bind_cpu_input("duration", np.ascontiguousarray(durations, dtype=np.float32))
        for out_meta in enc_sess.get_outputs():
            enc_iob.bind_output(out_meta.name, "webgpu")
        enc_sess.run_with_iobinding(enc_iob)
        x_seg_ov, x_est_ov, mask_t_ov = enc_iob.get_outputs()
        mask_t = mask_t_ov.numpy()

        if known_durations is not None and known_durations.size > 0:
            known_boundaries = np.zeros_like(mask_t, dtype=bool)
            for idx in range(batch_size):
                valid = known_durations[idx][known_durations[idx] > 0]
                if len(valid) == 0:
                    continue
                kb = self._sessions["dur2bd"].run(
                    None,
                    {
                        "durations": valid[np.newaxis, :].astype(np.float32, copy=False),
                        "maskT": mask_t[idx : idx + 1],
                    },
                )[0]
                known_boundaries[idx : idx + 1] = kb
        else:
            known_boundaries = np.zeros_like(mask_t, dtype=bool)

        known_boundaries = np.ascontiguousarray(known_boundaries, dtype=bool)
        lang_arr = np.full((batch_size,), int(language), dtype=np.int64)
        thr_arr = np.array(boundary_threshold, dtype=np.float32)
        rad_arr = np.array(boundary_radius, dtype=np.int64)
        seg_sess = self._sessions["segmenter"]
        seg_out_name = seg_sess.get_outputs()[0].name
        steps = ts if (self.loop and ts) else [0.0]

        cur_boundaries_ov = None
        for t in steps:
            seg_iob = seg_sess.io_binding()
            seg_iob.bind_ortvalue_input("x_seg", x_seg_ov)
            seg_iob.bind_cpu_input("language", lang_arr)
            seg_iob.bind_cpu_input("known_boundaries", known_boundaries)
            if cur_boundaries_ov is None:
                seg_iob.bind_cpu_input("prev_boundaries", known_boundaries)
            else:
                seg_iob.bind_ortvalue_input("prev_boundaries", cur_boundaries_ov)
            seg_iob.bind_cpu_input("t", np.full((batch_size,), float(t), dtype=np.float32))
            seg_iob.bind_ortvalue_input("maskT", mask_t_ov)
            seg_iob.bind_cpu_input("threshold", thr_arr)
            seg_iob.bind_cpu_input("radius", rad_arr)
            seg_iob.bind_output(seg_out_name, "webgpu")
            seg_sess.run_with_iobinding(seg_iob)
            cur_boundaries_ov = seg_iob.get_outputs()[0]

        boundaries = cur_boundaries_ov.numpy()
        durations_out, mask_n = self._sessions["bd2dur"].run(
            None,
            {
                "boundaries": boundaries,
                "maskT": mask_t,
            },
        )
        est_sess = self._sessions["estimator"]
        est_iob = est_sess.io_binding()
        est_iob.bind_ortvalue_input("x_est", x_est_ov)
        est_iob.bind_ortvalue_input("boundaries", cur_boundaries_ov)
        est_iob.bind_ortvalue_input("maskT", mask_t_ov)
        est_iob.bind_cpu_input("maskN", np.ascontiguousarray(mask_n))
        est_iob.bind_cpu_input("threshold", np.array(score_threshold, dtype=np.float32))
        for out_meta in est_sess.get_outputs():
            est_iob.bind_output(out_meta.name, "cpu")
        est_sess.run_with_iobinding(est_iob)
        presence, scores = [ov.numpy() for ov in est_iob.get_outputs()]
        est_iob.clear_binding_inputs()
        est_iob.clear_binding_outputs()
        enc_iob.clear_binding_inputs()
        enc_iob.clear_binding_outputs()

        results = []
        for idx in range(batch_size):
            valid = mask_n[idx].astype(bool)
            results.append((durations_out[idx][valid], presence[idx][valid], scores[idx][valid]))
        return results

    def infer_batch(
        self,
        *,
        waveforms: np.ndarray,
        durations: np.ndarray,
        known_durations: np.ndarray | None,
        boundary_threshold: float,
        boundary_radius: int,
        score_threshold: float,
        language: int = 0,
        ts: list[float] | None = None,
    ) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
        ts = list(ts or [])
        if self._use_webgpu_iobinding:
            try:
                return self._infer_batch_webgpu_iobinding(
                    waveforms=waveforms,
                    durations=durations,
                    known_durations=known_durations,
                    boundary_threshold=boundary_threshold,
                    boundary_radius=boundary_radius,
                    score_threshold=score_threshold,
                    language=language,
                    ts=ts,
                )
            except Exception:
                self._use_webgpu_iobinding = False
        batch_size = int(waveforms.shape[0])

        enc_out = self._sessions["encoder"].run(
            None,
            {
                "waveform": waveforms.astype(np.float32, copy=False),
                "duration": durations.astype(np.float32, copy=False),
            },
        )
        x_seg, x_est, mask_t = enc_out[0], enc_out[1], enc_out[2]

        if known_durations is not None and known_durations.size > 0:
            known_boundaries = np.zeros_like(mask_t, dtype=bool)
            for idx in range(batch_size):
                valid = known_durations[idx][known_durations[idx] > 0]
                if len(valid) == 0:
                    continue
                kb = self._sessions["dur2bd"].run(
                    None,
                    {
                        "durations": valid[np.newaxis, :].astype(np.float32, copy=False),
                        "maskT": mask_t[idx : idx + 1],
                    },
                )[0]
                known_boundaries[idx : idx + 1] = kb
        else:
            known_boundaries = np.zeros_like(mask_t, dtype=bool)

        boundaries = known_boundaries.copy()
        lang_arr = np.full((batch_size,), int(language), dtype=np.int64)

        if self.loop and ts:
            for t in ts:
                boundaries = self._sessions["segmenter"].run(
                    None,
                    {
                        "x_seg": x_seg,
                        "language": lang_arr,
                        "known_boundaries": known_boundaries,
                        "prev_boundaries": boundaries,
                        "t": np.full((batch_size,), float(t), dtype=np.float32),
                        "maskT": mask_t,
                        "threshold": np.array(boundary_threshold, dtype=np.float32),
                        "radius": np.array(boundary_radius, dtype=np.int64),
                    },
                )[0]
        else:
            boundaries = self._sessions["segmenter"].run(
                None,
                {
                    "x_seg": x_seg,
                    "language": lang_arr,
                    "known_boundaries": known_boundaries,
                    "prev_boundaries": boundaries,
                    "t": np.zeros((batch_size,), dtype=np.float32),
                    "maskT": mask_t,
                    "threshold": np.array(boundary_threshold, dtype=np.float32),
                    "radius": np.array(boundary_radius, dtype=np.int64),
                },
            )[0]

        durations_out, mask_n = self._sessions["bd2dur"].run(
            None,
            {
                "boundaries": boundaries,
                "maskT": mask_t,
            },
        )
        presence, scores = self._sessions["estimator"].run(
            None,
            {
                "x_est": x_est,
                "boundaries": boundaries,
                "maskT": mask_t,
                "maskN": mask_n,
                "threshold": np.array(score_threshold, dtype=np.float32),
            },
        )

        results = []
        for idx in range(batch_size):
            valid = mask_n[idx].astype(bool)
            results.append(
                (
                    durations_out[idx][valid],
                    presence[idx][valid],
                    scores[idx][valid],
                )
            )
        return results
