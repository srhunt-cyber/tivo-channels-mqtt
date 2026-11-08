import unittest
from unittest.mock import patch

from tivo_channels_mqtt import storage


class StorageParserTests(unittest.TestCase):
    def test_standard_data_mount(self):
        output = ('Filesystem 1K-blocks Used Available Use% Mounted on\n'
                  '/dev/block/dm-4 4194304 2097152 2097152 50% /data\n')
        self.assertEqual(storage.parse_free_mib(output), 2048)

    def test_android_resolved_mount_and_crlf(self):
        output = ('Filesystem 1K-blocks Used Available Use% Mounted on\r\n'
                  '/dev/block/dm-4 4194304 3670016 524288 88% /data/user/0\r\n')
        self.assertEqual(storage.parse_free_mib(output), 512)

    def test_wrapped_filesystem_and_mount_with_spaces(self):
        output = ('Filesystem 1024-blocks Used Available Capacity Mounted on\n'
                  '/dev/block/long-filesystem-name\n'
                  '4194304 3145728 1048576 75% /storage/path with spaces\n')
        self.assertEqual(storage.parse_free_mib(output), 1024)

    def test_zero_available_is_valid(self):
        self.assertEqual(storage.parse_free_mib('/dev/block/dm-4 4194304 4194304 0 100% /data/user/0'), 0)

    def test_unrecognized_or_ambiguous_output_fails_closed(self):
        for output in ('', 'error: device unauthorized',
                       'Filesystem 1K-blocks Used Available Use% Mounted on',
                       '/dev/block/dm-4 4096 1000 missing 50% /data',
                       '/dev/block/dm-4 4096 1000 99999 50% /data',
                       '/dev/block/dm-4 4096 1000 2000 50% /data\n'
                       '/dev/block/dm-5 4096 1000 2000 50% /other'):
            with self.subTest(output=output), self.assertRaises(RuntimeError):
                storage.parse_free_mib(output)

    def test_reads_the_requested_data_filesystem(self):
        with patch.object(storage,'adb',return_value='/dev/block/dm-4 4096 2048 2048 50% /data/user/0') as adb:
            self.assertEqual(storage.free_mib('192.0.2.101'), 2)
            adb.assert_called_once_with('192.0.2.101','shell','df','-k','/data')


if __name__ == '__main__':
    unittest.main()
