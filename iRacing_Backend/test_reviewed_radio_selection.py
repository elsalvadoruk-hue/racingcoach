"""Selection writes are isolated, complete and limited to existing short files."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from custom_voice_pack_format import FORMAT
from custom_voice_packs import CustomVoicePacks
from radio_audio import RadioAudioCache
from reviewed_radio import ReviewedRadio
from reviewed_radio_selection import CATALOG_FORMAT, FRONT_IDS, SELECTION_FORMAT, ShortRadioSelection, json_bytes, sha


def fixture(directory):
    directory.mkdir(parents=True, exist_ok=True)
    entries, metadata = [], {}
    for number, (key, take) in enumerate([('left', 1), ('left', 2), ('yellow', 1),
                                         ('class_traffic_ahead', 2), ('class_traffic_ahead', 4),
                                         ('class_traffic_ahead', 5)]):
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as output:
            output.setparams((1, 2, 24000, 0, 'NONE', ''))
            output.writeframes(bytes([number + 1, 0]) * 24000)
        content = stream.getvalue()
        digest = sha(content)
        name = f'{key}-take{take}'
        row = dict(id=name, role='spotter', sourceText=name, text=key, spoken=key,
                   eventKey=key, reviewedVoice='Fenrir', sourceTexts=[f'original {key}'],
                   audio=f'clips/{digest}.wav', sha256=digest, duration=1., takeNumber=take)
        path = directory / row['audio']
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        entries.append(row)
        metadata[name] = dict(priorApproved=take != 2 or key != 'left', kind='natural',
                              rootId=name, rootPcmSha256=digest)
    manifest = dict(format=FORMAT, schemaVersion=3, packId='reviewed', version='1',
                    name='Short review', targets=['spotter'], entries=entries)
    catalog = dict(format=CATALOG_FORMAT, schemaVersion=1, manifest=manifest, metadata=metadata)
    catalog['sha256'] = sha(json_bytes(catalog))
    (directory / 'manifest.json').write_bytes(json_bytes(manifest))
    (directory / 'review-catalog.json').write_bytes(json_bytes(catalog))
    return ShortRadioSelection(directory)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = fixture(self.root / 'reviewed_radio')
        state = self.store.state()
        self.selection = dict(format=SELECTION_FORMAT, schemaVersion=1,
            catalogSha256=state['catalog']['sha256'], activeManifestSha256=state['activeManifestSha256'],
            selectedIds=['left-take1', 'yellow-take1', *sorted(FRONT_IDS)],
            rejectedIds=['left-take2'], notes={'left-take2': 'Not selected after listening'})

    def files(self):
        return {path.relative_to(self.root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.root.rglob('*') if path.is_file()}

    def rejected_without_writes(self, selection):
        before = self.files()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.store.apply(selection)
        self.assertEqual(before, self.files())

    def test_apply_updates_short_manifest_only_and_keeps_audio_and_catalog(self):
        default = self.root / 'default.mp3'
        default.write_bytes(b'original default audio')
        before = self.files()
        result = self.store.apply(self.selection)
        self.assertEqual(result['selectedFiles'], 5)
        self.assertEqual(result['groups'], 3)
        state = self.store.state()
        self.assertEqual(set(state['activeIds']), set(self.selection['selectedIds']))
        self.assertEqual(state['previousSelection']['notes'], self.selection['notes'])
        for name, value in before.items():
            if not name.endswith('/manifest.json'):
                self.assertEqual(value, self.files()[name])
        self.assertEqual((self.store.directory / 'manifest.previous.json').read_bytes(),
                         before['reviewed_radio/manifest.json'][0])
        review = ReviewedRadio(self.store.directory)
        self.assertEqual(len(review.choices('spotter', 'Fenrir', 'left')), 1)
        self.assertTrue(review.audio('spotter', 'Fenrir', 'original yellow').startswith('data:audio/wav;'))

    def test_switching_to_default_ignores_selected_short_audio(self):
        self.store.apply(self.selection)
        cache = RadioAudioCache(Mock(), self.root / 'radio_audio')
        manager = CustomVoicePacks(self.root / 'custom', self.root / 'short')
        cache.custom_packs = manager
        name = cache.path('spotter', 'original left')
        name.parent.mkdir(exist_ok=True)
        name.write_bytes(b'default voice' * 20)
        baseline = cache.phrase('spotter', 'original left')
        key = 'standard-short-spotter-fenrir@2.5.0'
        manager.loaded = {key: ({}, {})}
        manager.selections = {'spotter': key}
        self.assertTrue(cache.phrase('spotter', 'original left').startswith('data:audio/wav;'))
        manager.selections = {}
        self.assertEqual(cache.phrase('spotter', 'original left'), baseline)
        cache.speech.synthesize.assert_not_called()

    def test_missing_group_cannot_silence_an_announcement(self):
        self.selection['selectedIds'].remove('yellow-take1')
        self.rejected_without_writes(self.selection)

    def test_front_three_original_ids_are_required(self):
        for name in FRONT_IDS:
            with self.subTest(name=name):
                value = deepcopy(self.selection)
                value['selectedIds'].remove(name)
                self.rejected_without_writes(value)

    def test_unknown_duplicate_or_rejected_selection_is_rejected(self):
        for values in (['unknown'], self.selection['selectedIds'] + ['left-take1'],
                       self.selection['selectedIds'] + ['left-take2']):
            with self.subTest(values=values):
                value = deepcopy(self.selection)
                value['selectedIds'] = values
                self.rejected_without_writes(value)

    def test_stale_active_manifest_or_different_catalog_cannot_overwrite(self):
        for name in ('activeManifestSha256', 'catalogSha256'):
            value = deepcopy(self.selection)
            value[name] = '0' * 64
            self.rejected_without_writes(value)

    def test_missing_or_corrupt_audio_cannot_modify_manifest(self):
        path = self.store.directory / self.store.catalog()['manifest']['entries'][0]['audio']
        original = path.read_bytes()
        path.write_bytes(b'corrupt')
        self.rejected_without_writes(self.selection)
        path.write_bytes(original)
        path.unlink()
        self.rejected_without_writes(self.selection)

    def test_validate_has_no_side_effects(self):
        before = self.files()
        manifest, state = self.store.validate(self.selection)
        self.assertEqual(len(manifest['entries']), 5)
        self.assertEqual(len(state['activeIds']), 6)
        self.assertEqual(before, self.files())

    def test_save_keeps_only_previous_approvals_for_still_selected_takes(self):
        previous = deepcopy(self.selection)
        previous['selectedIds'] = self.store.state()['activeIds']
        previous['rejectedIds'] = []
        previous['userApprovedIds'] = ['left-take1', 'left-take2', 'yellow-take1']
        previous['singleReplacement'] = {'oldId': 'old', 'newId': 'left-take1'}
        (self.store.directory / 'review-selection.json').write_bytes(json_bytes(previous))
        # The browser only sends the selection and notes, without approval metadata.
        self.store.apply(self.selection)
        saved = self.store.state()['previousSelection']
        self.assertEqual(saved['userApprovedIds'], ['left-take1', 'yellow-take1'])
        self.assertEqual(saved['singleReplacement'], previous['singleReplacement'])

    def test_import_cannot_invent_listening_approval(self):
        self.selection['userApprovedIds'] = list(self.selection['selectedIds'])
        self.store.apply(self.selection)
        self.assertEqual(self.store.state()['previousSelection'].get('userApprovedIds', []), [])

    def test_catalog_changed_wording_or_bad_digest_is_rejected(self):
        path = self.store.directory / 'review-catalog.json'
        catalog = self.store.catalog()
        for kind in ('digest', 'wording'):
            value = deepcopy(catalog)
            value['manifest']['entries'][1]['text'] = 'different sentence'
            value['manifest']['entries'][1]['spoken'] = 'different sentence'
            if kind == 'wording':
                value['sha256'] = sha(json_bytes({k: v for k, v in value.items() if k != 'sha256'}))
            path.write_bytes(json_bytes(value))
            self.rejected_without_writes(self.selection)

    def test_invalid_notes_are_rejected(self):
        for notes in ({'unknown': 'memo'}, {'left-take1': 'a' * 2001}, {'left-take1': 123}):
            value = deepcopy(self.selection)
            value['notes'] = notes
            self.rejected_without_writes(value)

    def test_symlink_directory_rejected_before_resolving(self):
        with patch.object(Path, 'is_symlink', lambda path: path.name == 'link'):
            with self.assertRaises(ValueError):
                ShortRadioSelection(self.root / 'link')

    def test_intermediate_junction_or_path_escape_is_rejected(self):
        with patch.object(Path, 'is_junction', lambda path: path.name == 'clips'):
            with self.assertRaises(ValueError):
                self.store._path('clips/anything.wav')
        with self.assertRaises(ValueError):
            self.store._path('../outside')

    def test_stale_previous_selection_is_not_automatically_reused(self):
        self.store.apply(self.selection)
        path = self.store.directory / 'review-selection.json'
        value = json.loads(path.read_bytes())
        value['activeManifestSha256'] = '0' * 64
        path.write_bytes(json_bytes(value))
        self.assertIsNone(self.store.state()['previousSelection'])

    def test_omitted_pending_take_is_retained_on_disk_but_not_played(self):
        self.selection['rejectedIds'] = []
        self.store.apply(self.selection)
        catalog = self.store.catalog()
        unused = next(row for row in catalog['manifest']['entries'] if row['id'] == 'left-take2')
        self.assertTrue((self.store.directory / unused['audio']).is_file())
        self.assertNotIn(unused['id'], self.store.state()['activeIds'])

    def _six_left_takes(self):
        catalog = self.store.catalog()
        for number in range(3, 7):
            stream = io.BytesIO()
            with wave.open(stream, 'wb') as output:
                output.setparams((1, 2, 24000, 0, 'NONE', ''))
                output.writeframes(bytes([number + 20, 0]) * 24000)
            content = stream.getvalue()
            row = deepcopy(catalog['manifest']['entries'][0])
            row.update(id=f'left-take{number}', sourceText=f'left-take{number}', takeNumber=number,
                       sha256=sha(content), audio=f'clips/{sha(content)}.wav')
            (self.store.directory / row['audio']).write_bytes(content)
            catalog['manifest']['entries'].append(row)
            catalog['metadata'][row['id']] = dict(priorApproved=False, kind='natural',
                                                 rootId=row['id'], rootPcmSha256=sha(content))
        catalog['sha256'] = sha(json_bytes({key: value for key, value in catalog.items() if key != 'sha256'}))
        (self.store.directory / 'review-catalog.json').write_bytes(json_bytes(catalog))
        (self.store.directory / 'manifest.json').write_bytes(json_bytes(catalog['manifest']))
        state = self.store.state()
        self.selection.update(catalogSha256=catalog['sha256'], activeManifestSha256=state['activeManifestSha256'],
                              rejectedIds=[], selectedIds=state['activeIds'])

    def test_six_takes_for_one_announcement_cannot_be_saved(self):
        self._six_left_takes()
        self.rejected_without_writes(self.selection)

    def test_five_takes_with_other_announcements_can_be_saved(self):
        self._six_left_takes()
        self.selection['selectedIds'].remove('left-take6')
        result = self.store.apply(self.selection)
        self.assertEqual(result['selectedFiles'], 9)
        self.assertEqual(len(ReviewedRadio(self.store.directory).choices('spotter', 'Fenrir', 'left')), 5)


if __name__ == '__main__':
    unittest.main()
