from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from opldeck.core.conversion import TRANSACTION_DIR, install_iso
from opldeck.library.deletion import delete_selection, prepare_delete
from opldeck.core.formats import Media
from opldeck.library.management import delete_ul, recover_ul
from opldeck.core.ul import read_cfg
from tests.fixtures import make_iso


class DeleteSelectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.library = self.root / 'usb'
        self.library.mkdir()
        self.entries = []
        for index in range(4):
            source = make_iso(self.root / f'Game {index}.iso', f'SLUS_100.{index:02}')
            self.entries.append(install_iso(source, self.library, media=Media.DVD))
        self.cfg = self.library / 'ul.cfg'
        self.ids = [entry.game_id for entry in self.entries]

    def plan(self, ids=None):
        return prepare_delete(self.cfg, self.ids[:3] if ids is None else ids)

    def test_deletes_selected_intact_empty_and_missing_preserving_unselected(self):
        (self.library / self.entries[1].part_name(0)).write_bytes(b'')
        (self.library / self.entries[2].part_name(0)).unlink()
        (self.library / 'ART').mkdir()
        cover = self.library / 'ART' / f'{self.ids[0]}_COV.jpg'
        cover.write_bytes(b'cover')
        plan = self.plan()
        events = []
        result = delete_selection(plan, progress=events.append)
        self.assertFalse(result.error)
        self.assertEqual(result.removed, tuple(self.ids[:3]))
        self.assertEqual(read_cfg(self.cfg), [self.entries[3]])
        self.assertTrue((self.library / self.entries[3].part_name(0)).exists())
        self.assertEqual(cover.read_bytes(), b'cover')
        self.assertEqual({event.index for event in events}, {1, 2, 3})

    def test_changed_index_aborts_before_any_delete(self):
        plan = self.plan()
        self.cfg.write_bytes(self.cfg.read_bytes() + self.entries[3].to_bytes())
        result = delete_selection(plan)
        self.assertIn('mudaram', result.error)
        self.assertFalse(result.removed)
        self.assertTrue(all((self.library / entry.part_name(0)).exists() for entry in self.entries))

    def test_changed_last_part_aborts_entire_plan_before_start(self):
        plan = self.plan()
        changed = self.library / self.entries[2].part_name(0)
        changed.write_bytes(b'changed after confirmation')
        result = delete_selection(plan)
        self.assertFalse(result.removed)
        self.assertTrue(result.error)
        self.assertEqual(read_cfg(self.cfg), self.entries)

    def test_symlink_later_in_selection_refuses_preview(self):
        path = self.library / self.entries[2].part_name(0)
        path.unlink()
        path.symlink_to(self.root / 'Game 2.iso')
        with self.assertRaises(ValueError):
            self.plan()
        self.assertEqual(read_cfg(self.cfg), self.entries)

    def test_repeated_or_missing_ids_refused(self):
        for ids in ([], [self.ids[0], self.ids[0]], ['SLUS_999.99']):
            with self.assertRaises(ValueError):
                self.plan(ids)
        self.assertEqual(read_cfg(self.cfg), self.entries)

    def test_cancellation_before_start_and_during_second_game(self):
        plan = self.plan()
        cancel = Event()
        cancel.set()
        result = delete_selection(plan, cancel=cancel)
        self.assertTrue(result.cancelled)
        self.assertFalse(result.removed)
        cancel.clear()
        def stop(event):
            if event.index == 2 and event.current.part == 1:
                cancel.set()
        result = delete_selection(plan, cancel=cancel, progress=stop)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.removed, (self.ids[0],))
        self.assertEqual(read_cfg(self.cfg), self.entries[1:])
        self.assertTrue((self.library / self.entries[1].part_name(0)).exists())
        self.assertFalse((self.library / TRANSACTION_DIR).exists())

    def test_failure_stops_later_games_and_reports_partial_completion(self):
        plan = self.plan()
        def fail_second(cfg, game_id, **kwargs):
            if game_id == self.ids[1]:
                raise OSError('simulated failure')
            return delete_ul(cfg, game_id, **kwargs)
        with patch('opldeck.library.deletion.delete_ul', side_effect=fail_second):
            result = delete_selection(plan)
        self.assertEqual(result.removed, (self.ids[0],))
        self.assertIn('simulated failure', result.error)
        self.assertEqual(read_cfg(self.cfg), self.entries[1:])

    def test_cleanup_failure_counts_committed_game_and_preserves_recoverability(self):
        plan = self.plan()
        with patch('opldeck.library.management._clean_journal', side_effect=OSError('cleanup failed')):
            result = delete_selection(plan)
        self.assertEqual(result.removed, (self.ids[0],))
        self.assertIn('já foi publicada', result.error)
        self.assertEqual(read_cfg(self.cfg), self.entries[1:])
        self.assertTrue((self.library / TRANSACTION_DIR).exists())
        recover_ul(self.library)
        self.assertFalse((self.library / TRANSACTION_DIR).exists())

    def test_change_between_games_is_checked_under_management_lock(self):
        plan = self.plan()
        changed = self.library / self.entries[1].part_name(0)
        def change(event):
            if event.index == 2 and event.current.part == 0:
                changed.write_bytes(b'external')
        result = delete_selection(plan, progress=change)
        self.assertEqual(result.removed, (self.ids[0],))
        self.assertIn('mudaram', result.error)
        self.assertEqual(changed.read_bytes(), b'external')
        self.assertEqual(read_cfg(self.cfg), self.entries[1:])
