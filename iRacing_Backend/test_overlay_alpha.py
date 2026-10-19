"""RGBA upload, opacity and GDI resource lifetime without opening desktop windows."""
import ctypes
from ctypes import wintypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from scripts.overlay_native import LayeredImage, NativeWindows


@unittest.skipUnless(hasattr(ctypes, 'WINFUNCTYPE'), 'Windows activation guard')
class PassiveWindowTests(unittest.TestCase):
    def test_startup_guard_vetoes_activation_on_only_its_thread_and_unhooks_on_failure(self):
        native = NativeWindows.__new__(NativeWindows)
        native.user = Mock()
        native.user.SetWindowsHookExW.return_value = 71
        native.user.CallNextHookEx.return_value = 23
        kernel = Mock()
        kernel.GetCurrentThreadId.return_value = 19
        with patch('scripts.overlay_native.ctypes.WinDLL', return_value=kernel):
            with self.assertRaisesRegex(ValueError, 'startup failed'):
                with native.block_activation():
                    arguments = native.user.SetWindowsHookExW.call_args.args
                    self.assertEqual((arguments[0], arguments[2], arguments[3]), (5, None, 19))
                    callback = arguments[1]
                    self.assertEqual(callback(5, 42, 0), 1)
                    self.assertEqual(callback(9, 42, 0), 1)
                    native.user.CallNextHookEx.assert_not_called()
                    self.assertEqual(callback(-1, 42, 0), 23)
                    raise ValueError('startup failed')
        native.user.UnhookWindowsHookEx.assert_called_once_with(71)

    def test_show_uses_native_nonactivating_map_and_standalone_guard_is_optional(self):
        native = NativeWindows.__new__(NativeWindows)
        native.user, native.handle = Mock(), Mock(return_value=42)
        widget = Mock()
        with native.block_activation(False):
            native.show_without_activation(widget)
        native.user.SetWindowsHookExW.assert_not_called()
        native.user.ShowWindow.assert_called_once_with(42, 4)
        widget.deiconify.assert_not_called()


class LayeredImageTests(unittest.TestCase):
    def layer(self):
        layer = LayeredImage.__new__(LayeredImage)
        layer.user, layer.gdi = Mock(), Mock()
        layer.native = SimpleNamespace(handle=lambda widget: 42, rounded_sizes={})
        layer.window, layer.dc, layer.bitmap, layer.original_bitmap = 21, 19, 17, 11
        layer.size, layer.offset, layer.clip = (2, 2), (0, 0), None
        layer.has_frame = layer.visible = False
        buffer = ctypes.create_string_buffer(16)
        layer.bits = ctypes.c_void_p(ctypes.addressof(buffer))
        return layer, buffer

    def test_upload_preserves_partial_alpha_and_premultiplies_color_without_a_matte(self):
        layer, buffer = self.layer()
        layer.sync = Mock()
        frame = Image.new('RGBA', (2, 2), (255, 80, 40, 128))
        frame.putpixel((0, 0), (255, 255, 255, 0))
        layer.present('owner', frame, (3, 4), 37, True)
        self.assertEqual(buffer.raw[:4], bytes((0, 0, 0, 0)))
        self.assertEqual(buffer.raw[4:8], bytes((20, 40, 128, 128)))
        self.assertEqual(layer.offset, (3, 4))
        layer.sync.assert_called_once_with('owner', 37, True)
        layer.gdi.CreateDIBSection.assert_not_called()

    def test_new_frame_replaces_transparent_pixels_and_reuses_the_same_bitmap(self):
        layer, buffer = self.layer()
        layer.sync = Mock()
        layer.present('owner', Image.new('RGBA', (2, 2), 'red'), (0, 0), 100, True)
        self.assertEqual(buffer.raw, bytes((0, 0, 255, 255)) * 4)
        layer.present('owner', Image.new('RGBA', (2, 2)), (0, 0), 100, True)
        self.assertEqual(buffer.raw, bytes(16))
        layer.gdi.CreateDIBSection.assert_not_called()
        layer.gdi.DeleteObject.assert_not_called()

    def test_resize_releases_old_bitmap_and_close_restores_the_original_gdi_object(self):
        layer, old_buffer = self.layer()
        layer.sync = Mock()
        resized = ctypes.create_string_buffer(24)
        def allocate(dc, info, usage, bits, section, offset):
            self.assertEqual((info._obj.header.width, info._obj.header.height), (3, -2))
            bits._obj.value = ctypes.addressof(resized)
            return 23
        layer.gdi.CreateDIBSection.side_effect = allocate
        layer.gdi.SelectObject.return_value = 17
        layer.present('owner', Image.new('RGBA', (3, 2), 'blue'), (0, 0), 100, True)
        self.assertEqual(resized.raw, bytes((255, 0, 0, 255)) * 6)
        layer.gdi.DeleteObject.assert_called_once_with(17)
        layer.close()
        self.assertEqual(layer.gdi.SelectObject.call_args.args, (19, 11))
        self.assertEqual([call.args[0] for call in layer.gdi.DeleteObject.call_args_list], [17, 23])
        layer.gdi.DeleteDC.assert_called_once_with(19)
        layer.user.DestroyWindow.assert_called_once_with(21)
        layer.close()
        layer.user.DestroyWindow.assert_called_once()

    def test_move_and_opacity_use_the_content_setting_and_hide_never_reactivates_the_window(self):
        layer, buffer = self.layer()
        layer.has_frame, layer.offset = True, (3, 4)
        def location(owner, output):
            output._obj.left, output._obj.top = -700, 120
            return True
        layer.user.GetWindowRect.side_effect = location
        layer.user.UpdateLayeredWindow.return_value = True
        layer.sync('owner', 37, True)
        args = layer.user.UpdateLayeredWindow.call_args.args
        self.assertEqual((args[2]._obj.x, args[2]._obj.y), (-697, 124))
        self.assertEqual(args[7]._obj.opacity, round(37 * 255 / 100))
        self.assertEqual(args[7]._obj.alpha, 1)
        layer.user.ShowWindow.assert_called_once_with(21, 4)
        layer.sync('owner', 37, False)
        self.assertEqual(layer.user.ShowWindow.call_args.args, (21, 0))
        self.assertFalse(layer.visible)
        layer.user.UpdateLayeredWindow.assert_called_once()
        layer.clear()
        self.assertFalse(layer.has_frame)

    @unittest.skipUnless(hasattr(ctypes, 'WinError'), 'Win32 error reporting')
    def test_failed_resize_frees_the_new_bitmap_and_keeps_the_previous_surface(self):
        layer, buffer = self.layer()
        layer.gdi.CreateDIBSection.return_value = 23
        layer.gdi.SelectObject.return_value = None
        with self.assertRaises(OSError):
            layer.present('owner', Image.new('RGBA', (3, 2)), (0, 0), 100, True)
        layer.gdi.DeleteObject.assert_called_once_with(23)
        self.assertEqual(layer.bitmap, 17)
        self.assertEqual(layer.size, (2, 2))


if __name__ == '__main__':
    unittest.main()
