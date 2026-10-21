/* SDK-independent Windows lock/copy primitive. No game declarations here. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <string.h>

__declspec(dllexport) int lmu_lock_version(void) {
    return 1;
}

__declspec(dllexport) int lmu_copy_locked(
    volatile LONG* busy, volatile LONG* waiters, HANDLE event,
    const void* src, void* dst, size_t size, unsigned spins
) {
    unsigned i;
    for (i = 0; i < spins; ++i) {
        if (InterlockedCompareExchange(busy, 1, 0) == 0) {
            memcpy(dst, src, size);
            InterlockedExchange(busy, 0);
            if (InterlockedCompareExchange(waiters, 0, 0) > 0 && event) {
                SetEvent(event);
            }
            return 1;
        }
        YieldProcessor();
    }
    return 0;
}
