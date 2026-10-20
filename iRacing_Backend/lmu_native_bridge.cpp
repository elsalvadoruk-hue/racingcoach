// LMU SDK headers remain in the game installation; never redistribute them.
#define NOMINMAX
#include <windows.h>
#include <tlhelp32.h>
#include <cstdint>
#include <cstring>
#include <utility>
#include <string>
#include <sstream>
#include <iomanip>
#include <memory>
#include <optional>
#include <cstddef>
#include "SharedMemoryInterface.hpp"

#ifndef LMU_HEADER_FINGERPRINT
#error LMU_HEADER_FINGERPRINT is required
#endif
static_assert(sizeof(void*) == 8, "Only the x64 SDK ABI is supported");
static_assert(sizeof(long) == 4 && sizeof(bool) == 1, "Unsupported Windows ABI");
namespace {
thread_local std::string error;
struct Handle {
    HANDLE h = nullptr;
    ~Handle() { if (h && h != INVALID_HANDLE_VALUE) CloseHandle(h); }
};
// ABI facts of the installed SDK's private lock control block. No object is
// created or initialized here. These words are used only in an existing map.
struct ExistingLockWords { volatile LONG waiters; volatile LONG busy; };
static_assert(sizeof(ExistingLockWords)==8 && offsetof(ExistingLockWords,busy)==4,
              "Unsupported SDK lock ABI");
struct Source {
    Handle process, mapping, lockMapping, lockEvent;
    SharedMemoryLayout* view = nullptr;
    ExistingLockWords* lockWords = nullptr;
    std::optional<SharedMemoryUpdateWaiter> waiter;
    SharedMemoryObjectOut copy{};
    bool telemetry = false, scoring = false;
    bool playerKnown = false;
    long playerId = -1, gameVersion = 0;
    std::string playerModel;
    unsigned long long processIdentity = 0, epoch = 0;
    DWORD pid = 0;
    ~Source() { if (view) UnmapViewOfFile(view); if(lockWords) UnmapViewOfFile(lockWords); }
};
const char* names[] = {"SME_ENTER", "SME_EXIT", "SME_STARTUP", "SME_SHUTDOWN", "SME_LOAD", "SME_UNLOAD", "SME_START_SESSION", "SME_END_SESSION", "SME_ENTER_REALTIME", "SME_EXIT_REALTIME", "SME_UPDATE_SCORING", "SME_UPDATE_TELEMETRY", "SME_INIT_APPLICATION", "SME_UNINIT_APPLICATION", "SME_SET_ENVIRONMENT", "SME_FFB"};
bool alive(Source* s) { return WaitForSingleObject(s->process.h, 0) == WAIT_TIMEOUT; }
bool has(const SharedMemoryGeneric& g, SharedMemoryEvent event) {
    for (auto entry : g.events) if (entry == event) return true;
    return false;
}
bool try_lock_existing(ExistingLockWords* words, unsigned spins=1000) {
    if(!words) return false;
    for(unsigned i=0;i<spins;++i) {
        if(InterlockedCompareExchange(&words->busy,1,0)==0) return true;
        YieldProcessor();
    }
    return false;
}
void unlock_existing(ExistingLockWords* words, HANDLE event) {
    InterlockedExchange(&words->busy,0);
    if(InterlockedCompareExchange(&words->waiters,0,0)>0 && event) SetEvent(event);
}
void reset_cache(Source* s) {
    s->telemetry=false;s->scoring=false;s->playerKnown=false;
    s->playerId=-1;s->playerModel.clear();++s->epoch;
}
// Called only while holding the game-created lock. This is the same helper
// exercised by the native fixture; lengths are checked before the SDK copy.
bool validate_and_copy(Source* s, SharedMemoryObjectOut& src, bool& tu, bool& su) {
    su=has(src.generic,SME_UPDATE_SCORING);tu=has(src.generic,SME_UPDATE_TELEMETRY);
    if(src.generic.gameVersion<=0) { error="GAME_VERSION_INVALID";return false; }
    if(s->gameVersion && s->gameVersion!=src.generic.gameVersion) { error="GAME_VERSION_CHANGED";return false; }
    if ((su && (src.scoring.scoringInfo.mNumVehicles<0 || src.scoring.scoringInfo.mNumVehicles>104 || src.scoring.scoringStreamSize>=sizeof(src.scoring.scoringStream))) ||
        (tu && src.telemetry.activeVehicles>104)) { error="SHARED_LAYOUT_INVALID";return false; }
    s->gameVersion=src.generic.gameVersion;
    bool reset=has(src.generic,SME_START_SESSION)||has(src.generic,SME_END_SESSION)||has(src.generic,SME_UNLOAD)||has(src.generic,SME_SHUTDOWN);
    if(su && s->scoring) {
        auto& old=s->copy.scoring.scoringInfo;auto& current=src.scoring.scoringInfo;
        reset=reset || old.mSession!=current.mSession || current.mCurrentET+0.001<old.mCurrentET;
    }
    const TelemInfoV01* incoming=nullptr;
    if(tu && src.telemetry.playerHasVehicle && src.telemetry.playerVehicleIdx<src.telemetry.activeVehicles)
        incoming=&src.telemetry.telemInfo[src.telemetry.playerVehicleIdx];
    std::string model;
    if(incoming) model.assign(incoming->mVehicleModel,strnlen(incoming->mVehicleModel,sizeof(incoming->mVehicleModel)));
    if(tu) {
        if(incoming) {
            reset=reset || (s->playerKnown && (s->playerId!=incoming->mID || s->playerModel!=model));
            if(s->telemetry && s->copy.telemetry.playerHasVehicle && s->copy.telemetry.playerVehicleIdx<s->copy.telemetry.activeVehicles)
                reset=reset || incoming->mElapsedTime+0.001<s->copy.telemetry.telemInfo[s->copy.telemetry.playerVehicleIdx].mElapsedTime;
        } else reset=reset || s->playerKnown || s->telemetry;
    }
    if(reset) reset_cache(s);
    CopySharedMemoryObj(s->copy,src);
    if(tu) {
        s->telemetry=incoming!=nullptr;
        s->playerKnown=incoming!=nullptr;
        s->playerId=incoming?incoming->mID:-1;s->playerModel=model;
    }
    if(su)s->scoring=true;
    return true;
}
int copy_existing_frame(Source* s, SharedMemoryObjectOut& src, ExistingLockWords* words,
                        HANDLE event, bool& tu, bool& su) {
    if(!try_lock_existing(words))return 0;
    struct Release { ExistingLockWords* words; HANDLE event;
        ~Release() { unlock_existing(words,event); }
    } release{words,event};
    try { return validate_and_copy(s,src,tu,su)?1:-1; }
    catch(...) { error="NATIVE_READ_FAILED";return -1; }
}
DWORD game_pid() {
    Handle list; list.h = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (list.h == INVALID_HANDLE_VALUE) return 0;
    PROCESSENTRY32W p{}; p.dwSize = sizeof(p);
    DWORD found = 0;
    if (Process32FirstW(list.h, &p)) do {
        if (_wcsicmp(p.szExeFile, L"Le Mans Ultimate.exe") == 0) {
            if (found) { error = "MULTIPLE_GAME_PROCESSES"; return 0; }
            found = p.th32ProcessID;
        }
    } while (Process32NextW(list.h, &p));
    return found;
}
void number(std::ostringstream& o, double value) {
    if (std::isfinite(value)) o << std::setprecision(17) << value;
    else o << "null";
}
bool put(const std::string& value, char* output, size_t size) {
    if (!output || value.size() + 1 > size) { error = "BUFFER_TOO_SMALL"; return false; }
    memcpy(output, value.c_str(), value.size() + 1); return true;
}
std::string snapshot(Source* s, bool tu, bool su) {
    auto& c = s->copy;
    const TelemInfoV01* t = nullptr;
    const VehicleScoringInfoV01* v = nullptr;
    if (s->telemetry && c.telemetry.playerHasVehicle && c.telemetry.playerVehicleIdx < c.telemetry.activeVehicles)
        t = &c.telemetry.telemInfo[c.telemetry.playerVehicleIdx];
    // Scoring is lower frequency. Reject stale/future cache combinations instead
    // of attributing a reused slot's old lap counters to a new telemetry sample.
    const double scoringAge=t?t->mElapsedTime-c.scoring.scoringInfo.mCurrentET:0;
    const bool aligned=s->scoring && t && std::isfinite(scoringAge) && scoringAge>=-0.25 && scoringAge<=2.0;
    if (aligned) for (long i=0; i<c.scoring.scoringInfo.mNumVehicles; ++i)
        if (c.scoring.vehScoringInfo[i].mID == t->mID && c.scoring.vehScoringInfo[i].mIsPlayer) {
            if (v) { v = nullptr; break; }
            v = &c.scoring.vehScoringInfo[i];
        }
    auto& si = c.scoring.scoringInfo;
    std::ostringstream o;
    o << "{\"game_version\":" << c.generic.gameVersion << ",\"events\":[";
    bool first = true;
    for (unsigned i=0;i<SME_MAX;++i) if (has(c.generic, static_cast<SharedMemoryEvent>(i))) {
        if (!first) o << ','; first=false; o << '"' << names[i] << '"';
    }
    o << "],\"session_id\":\"" << s->pid << ':' << s->processIdentity << ':' << s->epoch << ':';
    if (s->scoring) o << si.mSession; else o << "unknown";
    o << "\",\"telemetry_updated\":" << (tu?"true":"false") << ",\"scoring_updated\":" << (su?"true":"false");
    o << ",\"vehicle_id\":"; if(t) o<<t->mID; else o<<"null";
#define NUM(KEY, VALID, VALUE) o << ",\"" KEY "\":"; if (VALID) number(o, VALUE); else o<<"null"
#define BOOL(KEY, VALID, VALUE) o << ",\"" KEY "\":"; if (VALID) o<<((VALUE)?"true":"false"); else o<<"null"
    NUM("telemetry_et",t,t->mElapsedTime);
    NUM("scoring_et",s->scoring,si.mCurrentET);
    NUM("lap_start_et",t,t->mLapStartET);
    NUM("scoring_lap_start_et",v,v->mLapStartET);
    NUM("total_laps",v,v->mTotalLaps);
    NUM("last_lap_time",v,v->mLastLapTime);
    BOOL("lap_invalidated",t,t->mLapInvalidated);
    NUM("count_lap_flag",v,static_cast<unsigned>(v->mCountLapFlag));
    NUM("track_limits_steps",t,static_cast<unsigned>(t->mTrackLimitsSteps));
    BOOL("in_pits",v,v->mInPits);
    BOOL("realtime",s->scoring,si.mInRealtime);
    BOOL("paused",s->scoring,si.mGamePhase == 9);
    NUM("speed_mps",t,std::hypot(t->mLocalVel.x,t->mLocalVel.y,t->mLocalVel.z));
    NUM("throttle_ratio",t,t->mUnfilteredThrottle);
    NUM("brake_ratio",t,t->mUnfilteredBrake);
    NUM("steering_ratio",t,t->mUnfilteredSteering);
#undef NUM
#undef BOOL
    o << '}'; return o.str();
}
}
extern "C" {
__declspec(dllexport) const char* lmu_error() { return error.c_str(); }
__declspec(dllexport) int lmu_abi(char* out, size_t size) {
    std::ostringstream o;
    o << "{\"bridge_version\":1,\"pointer_size\":" << sizeof(void*)
      << ",\"header_fingerprint\":\"" LMU_HEADER_FINGERPRINT "\",\"sizes\":{";
#define SIZE(T) o << "\"" #T "\":" << sizeof(T)
    SIZE(SharedMemoryLayout); o<<','; SIZE(SharedMemoryObjectOut); o<<',';
    SIZE(TelemInfoV01); o<<','; SIZE(VehicleScoringInfoV01); o<<','; SIZE(ScoringInfoV01);
#undef SIZE
    o << "},\"offsets\":{\"telemetry\":" << offsetof(SharedMemoryObjectOut,telemetry)
      << ",\"scoring\":" << offsetof(SharedMemoryObjectOut,scoring)
      << ",\"telemInfo\":" << offsetof(SharedMemoryTelemetryData,telemInfo)
      << ",\"lap_invalidated\":" << offsetof(TelemInfoV01,mLapInvalidated)
      << ",\"count_lap_flag\":" << offsetof(VehicleScoringInfoV01,mCountLapFlag)
      << "},\"lock_abi\":{\"size\":" << sizeof(ExistingLockWords)
      << ",\"busy_offset\":" << offsetof(ExistingLockWords,busy) << "}}";
    return put(o.str(),out,size)?1:-1;
}
__declspec(dllexport) void* lmu_open() {
    error.clear(); auto s=std::make_unique<Source>();
    s->pid=game_pid();
    if (!s->pid) { if(error.empty())error="GAME_NOT_RUNNING"; return nullptr; }
    s->process.h=OpenProcess(SYNCHRONIZE|PROCESS_QUERY_LIMITED_INFORMATION,FALSE,s->pid);
    if(!s->process.h) { error="GAME_PROCESS_UNAVAILABLE"; return nullptr; }
    FILETIME created,exit,kernel,user;
    if(!GetProcessTimes(s->process.h,&created,&exit,&kernel,&user)) { error="GAME_PROCESS_UNAVAILABLE"; return nullptr; }
    s->processIdentity=(static_cast<unsigned long long>(created.dwHighDateTime)<<32)|created.dwLowDateTime;
    s->mapping.h=OpenFileMappingW(FILE_MAP_READ,FALSE,L"LMU_Data");
    if(!s->mapping.h) { error="SHARED_MEMORY_UNAVAILABLE"; return nullptr; }
    // Open only existing synchronization objects. Do not call the SDK lock
    // factory: its initialization can reset shared lock words after MapView.
    s->lockMapping.h=OpenFileMappingA(FILE_MAP_ALL_ACCESS,FALSE,"LMU_SharedMemoryLockData");
    s->lockEvent.h=OpenEventA(SYNCHRONIZE|EVENT_MODIFY_STATE,FALSE,"LMU_SharedMemoryLockEvent");
    if(!s->lockMapping.h||!s->lockEvent.h) { error="SHARED_LOCK_UNAVAILABLE"; return nullptr; }
    s->waiter=SharedMemoryUpdateWaiter::MakeSharedMemoryUpdateWaiter();
    if(!s->waiter) { error="SHARED_EVENTS_UNAVAILABLE"; return nullptr; }
    s->view=static_cast<SharedMemoryLayout*>(MapViewOfFile(s->mapping.h,FILE_MAP_READ,0,0,sizeof(SharedMemoryLayout)));
    if(!s->view) { error="SHARED_LAYOUT_UNAVAILABLE"; return nullptr; }
    s->lockWords=static_cast<ExistingLockWords*>(MapViewOfFile(s->lockMapping.h,FILE_MAP_ALL_ACCESS,0,0,sizeof(ExistingLockWords)));
    if(!s->lockWords) { error="SHARED_LOCK_UNAVAILABLE"; return nullptr; }
    if(!alive(s.get())) { error="GAME_EXITED"; return nullptr; }
    return s.release();
}
__declspec(dllexport) int lmu_read(void* handle, unsigned timeout, char* output,size_t size) {
    auto* s=static_cast<Source*>(handle);
    if(!s) { error="NOT_OPEN"; return -1; }
    if(!alive(s)) { error="GAME_EXITED"; return -1; }
    // The SDK waiter can wait twice. Divide the requested budget between its gates.
    if(!s->waiter->WaitForNextFrame(timeout/2)) {
        if(!alive(s)) { error="GAME_EXITED"; return -1; }
        return 0;
    }
    auto& src=s->view->data;
    bool tu=false,su=false;
    const int copied=copy_existing_frame(s,src,s->lockWords,s->lockEvent.h,tu,su);
    if(copied!=1)return copied;
    if(!alive(s)) { error="GAME_EXITED"; return -1; }
    return put(snapshot(s,tu,su),output,size)?1:-1;
}
__declspec(dllexport) void lmu_close(void* handle) { delete static_cast<Source*>(handle); }
}
