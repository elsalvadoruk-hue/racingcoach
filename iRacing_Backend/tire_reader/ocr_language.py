"""English Windows OCR for the tire overlay: detect it, and request it only on the user's click.

Windows OCR languages are a Windows feature (Features on Demand), not an app file,
so they cannot ship in the release ZIP. Adding one needs administrator approval
(UAC) and Windows Update. Declining or failing never blocks the app: the overlay
keeps using the installed OCR language, with dot/format checks.
"""
import sys

CAPABILITY = 'Language.OCR~~~en-US~0.0.1.0'
POWERSHELL_ARGS = ('-NoProfile -ExecutionPolicy Bypass -Command '
                   f'"Add-WindowsCapability -Online -Name {CAPABILITY}"')


def languages():
    from winrt.windows.media.ocr import OcrEngine
    return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]


def has_english(tags):
    return any(isinstance(tag, str) and tag.lower().split('-')[0] == 'en' for tag in tags or ())


def request_install(*, wait=False):
    """Show the UAC prompt for one PowerShell command. Returns the exit code when waiting."""
    if sys.platform != 'win32':
        raise RuntimeError('Windows에서만 설치할 수 있습니다.')
    import ctypes
    from ctypes import wintypes as w

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [('cbSize', w.DWORD), ('fMask', ctypes.c_ulong), ('hwnd', w.HWND),
                    ('lpVerb', w.LPCWSTR), ('lpFile', w.LPCWSTR), ('lpParameters', w.LPCWSTR),
                    ('lpDirectory', w.LPCWSTR), ('nShow', ctypes.c_int), ('hInstApp', w.HINSTANCE),
                    ('lpIDList', ctypes.c_void_p), ('lpClass', w.LPCWSTR), ('hkeyClass', w.HKEY),
                    ('dwHotKey', w.DWORD), ('hIconOrMonitor', w.HANDLE), ('hProcess', w.HANDLE)]

    info = SHELLEXECUTEINFOW(cbSize=ctypes.sizeof(SHELLEXECUTEINFOW), fMask=0x40,  # NOCLOSEPROCESS
                             lpVerb='runas', lpFile='powershell.exe', lpParameters=POWERSHELL_ARGS, nShow=1)
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell.ShellExecuteExW.restype = w.BOOL
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        raise RuntimeError('관리자 승인이 취소되었습니다.' if error == 1223 else f'설치를 시작하지 못했습니다. ({error})')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    try:
        if not wait or not info.hProcess:
            return None
        kernel.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        code = w.DWORD()
        kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        if info.hProcess:
            kernel.CloseHandle(info.hProcess)


def recheck():
    """A fresh process reads the language list Windows has after an install."""
    import subprocess
    code = ('import sys;sys.path.insert(0,sys.argv[1]);from tire_reader.ocr_language import languages,has_english;'
            'print(int(has_english(languages())))')
    from pathlib import Path
    result = subprocess.run([sys.executable, '-c', code, str(Path(__file__).resolve().parents[1])],
                            capture_output=True, text=True, timeout=60)
    return result.stdout.strip() == '1'


def ensure_interactive():
    """Installer step. Optional: every outcome continues the installation."""
    try:
        tags = languages()
    except Exception as error:  # winrt missing or Windows OCR unavailable
        print(f'Windows OCR check skipped ({type(error).__name__}). The tire overlay will report it in the app.')
        return 0
    if has_english(tags):
        print('English Windows OCR is available.')
        return 0
    print(f'Windows OCR languages: {", ".join(tags) or "none"}.')
    print('The tire overlay reads dashboard numbers more reliably with English Windows OCR.')
    print('It adds only the OCR component; the Windows display language does not change.')
    answer = input('Install English Windows OCR now? Windows will ask for administrator approval. [Y/N] ')
    if answer.strip().lower() not in ('y', 'yes'):
        print('Skipped. You can install it later from the tire overlay settings in the app.')
        return 0
    try:
        code = request_install(wait=True)
    except RuntimeError as error:
        print(f'Not installed: {error} You can install it later from the app.')
        return 0
    print('English Windows OCR is installed.' if code == 0 and recheck() else
          'English Windows OCR could not be confirmed. The app keeps working; you can retry from its settings.')
    return 0


if __name__ == '__main__':
    raise SystemExit(ensure_interactive())
