from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from opldeck.core.batch import TransferJob, run_batch
from opldeck.core.conversion import install_iso
from opldeck.core.formats import Media
from opldeck.core.ul import read_cfg
from tests.fixtures import make_bin, make_iso


class BatchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.destination = self.root / "usb"
        self.destination.mkdir()
        self.jobs = []
        for i in range(5):
            game_id = f"SLUS_100.{i:02}"
            source = make_iso(self.root / f"Game {i}.iso", game_id)
            self.jobs.append(TransferJob("install", source, self.destination, game_id,
                                         f"Game {i}", source.stat().st_size, Media.DVD))

    def test_five_games_install_export_roundtrip_and_progress(self):
        events = []
        result = run_batch(self.jobs, progress=events.append)
        self.assertFalse(result.error)
        self.assertEqual(len(result.completed), 5)
        self.assertEqual(result.pending, 0)
        self.assertEqual(len(read_cfg(self.destination / "ul.cfg")), 5)
        self.assertEqual([event.index for event in events if event.phase == "preparing"], [1, 2, 3, 4, 5])
        overall = [event.overall_completed for event in events]
        self.assertEqual(overall, sorted(overall))
        self.assertEqual(events[-1].overall_completed, sum(job.size for job in self.jobs))
        exports = [replace(job, kind="export", source=self.destination / "ul.cfg",
                           destination=self.root / f"{job.game_id}.iso") for job in self.jobs]
        result = run_batch(exports)
        self.assertFalse(result.error)
        for original, output in zip(self.jobs, result.completed):
            self.assertEqual(output.read_bytes(), original.source.read_bytes())

    def test_total_is_weighted_by_bytes_and_resets_only_current(self):
        jobs = self.jobs[:2]
        with jobs[1].source.open("ab") as stream:
            stream.write(bytes(jobs[1].size * 3))
        jobs[1] = replace(jobs[1], size=jobs[1].size * 4)
        events = []
        result = run_batch(jobs, progress=events.append)
        self.assertFalse(result.error)
        second = next(e for e in events if e.index == 2 and e.phase == "preparing")
        self.assertEqual(second.current.completed, 0)
        self.assertEqual(second.overall_completed * 100 // second.overall_total, 20)

    def test_cancellation_during_second_game_keeps_first_and_cleans_partial(self):
        cancel = Event()

        def report(event):
            if event.index == 2 and event.current.completed:
                cancel.set()

        result = run_batch(self.jobs, cancel=cancel, progress=report)
        self.assertTrue(result.cancelled)
        self.assertEqual(len(result.completed), 1)
        self.assertEqual(result.attempted, 2)
        self.assertEqual(result.pending, 3)
        entries = read_cfg(self.destination / "ul.cfg")
        self.assertEqual([e.game_id for e in entries], [self.jobs[0].game_id])
        self.assertEqual({p.name for p in self.destination.iterdir()}, {"ul.cfg", entries[0].part_name(0)})

    def test_cancellation_between_games_does_not_start_next(self):
        cancel = Event()

        def report(event):
            if event.phase == "complete":
                cancel.set()

        result = run_batch(self.jobs, cancel=cancel, progress=report)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.attempted, 1)
        self.assertEqual(result.pending, 4)
        self.assertEqual(len(read_cfg(self.destination / "ul.cfg")), 1)

    def test_failure_stops_queue_and_preserves_notes(self):
        def transfer(source, *args, **kwargs):
            if source == self.jobs[1].source:
                exc = OSError("Falha de escrita")
                exc.add_note("Detalhe de recuperação")
                raise exc
            return install_iso(source, *args, **kwargs)

        with patch("opldeck.core.batch.install_iso", side_effect=transfer):
            result = run_batch(self.jobs)
        self.assertEqual(len(result.completed), 1)
        self.assertEqual(result.pending, 3)
        self.assertIn("Detalhe de recuperação", result.error)
        self.assertEqual(len(read_cfg(self.destination / "ul.cfg")), 1)

    def test_preflight_duplicates_and_destination_collision_write_nothing(self):
        for changed in (replace(self.jobs[1], game_id=self.jobs[0].game_id),
                        replace(self.jobs[1], title=self.jobs[0].title)):
            result = run_batch([self.jobs[0], changed])
            self.assertTrue(result.error)
            self.assertEqual(result.attempted, 0)
            self.assertEqual(list(self.destination.iterdir()), [])
        entry = install_iso(self.jobs[-1].source, self.destination, media=Media.DVD)
        before = {p.name: p.read_bytes() for p in self.destination.iterdir()}
        result = run_batch(self.jobs)
        self.assertTrue(result.error)
        self.assertEqual(result.pending, 5)
        self.assertEqual({p.name: p.read_bytes() for p in self.destination.iterdir()}, before)
        self.assertEqual(read_cfg(self.destination / "ul.cfg")[0], entry)

    def test_whole_batch_space_check_and_cancel_before_start(self):
        usage = shutil.disk_usage(self.destination)
        with patch("opldeck.core.conversion.shutil.disk_usage", return_value=usage._replace(free=self.jobs[0].size + 65536)):
            result = run_batch(self.jobs)
        self.assertIn("Espaço insuficiente", result.error)
        self.assertEqual(result.attempted, 0)
        cancel = Event()
        cancel.set()
        result = run_batch(self.jobs, cancel=cancel)
        self.assertTrue(result.cancelled)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_changed_source_in_preflight_and_between_games(self):
        result = run_batch([replace(self.jobs[0], size=1)])
        self.assertIn("alterada", result.error)
        self.assertEqual(list(self.destination.iterdir()), [])

        def report(event):
            if event.index == 1 and event.phase == "complete":
                make_iso(self.jobs[1].source, "SLUS_999.99")

        result = run_batch(self.jobs, progress=report)
        self.assertIn("alterada", result.error)
        self.assertEqual(len(result.completed), 1)
        self.assertEqual(result.pending, 3)

    def test_bin_batch_output_and_existing_file_preflight(self):
        jobs = []
        for job in self.jobs[:2]:
            raw = make_bin(job.source, job.source.with_suffix(".bin"))
            jobs.append(replace(job, kind="bin", source=raw, destination=self.destination / f"{job.game_id}.iso"))
        jobs[1].destination.write_bytes(b"keep")
        result = run_batch(jobs)
        self.assertTrue(result.error)
        self.assertFalse(jobs[0].destination.exists())
        self.assertEqual(jobs[1].destination.read_bytes(), b"keep")
        jobs[1].destination.unlink()
        result = run_batch(jobs)
        self.assertFalse(result.error)
        for source, output in zip(self.jobs, result.completed):
            self.assertEqual(source.source.read_bytes(), output.read_bytes())

    def test_export_missing_part_fails_before_first_output(self):
        result = run_batch(self.jobs)
        (self.destination / result.completed[1].part_name(0)).unlink()
        exports = [replace(job, kind="export", source=self.destination / "ul.cfg",
                           destination=self.root / f"{job.game_id}.iso") for job in self.jobs]
        result = run_batch(exports)
        self.assertTrue(result.error)
        self.assertEqual(result.attempted, 0)
        self.assertTrue(all(not job.destination.exists() for job in exports))
