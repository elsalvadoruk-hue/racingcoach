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
void text_json(std::ostringstream& o,const char* value,size_t size) {
    o<<'"';
    for(size_t i=0;i<size && value[i];++i) {
        unsigned char c=static_cast<unsigned char>(value[i]);
        if(c=='"' || c=='\\')o<<'\\'<<char(c);
        else if(c<32)o<<"\\u00"<<"0123456789abcdef"[c>>4]<<"0123456789abcdef"[c&15];
        else o<<char(c);
    }
    o<<'"';
}
bool put(const std::string& value, char* output, size_t size) {
    if (!output || value.size() + 1 > size) { error = "BUFFER_TOO_SMALL"; return false; }
    memcpy(output, value.c_str(), value.size() + 1); return true;
}
bool finite_position(const TelemVect3& p) {
    return std::isfinite(p.x) && std::isfinite(p.y) && std::isfinite(p.z);
}
bool valid_orientation(const TelemVect3 (&rows)[3]) {
    for(unsigned i=0;i<3;++i) {
        if(!finite_position(rows[i]))return false;
        for(unsigned j=0;j<3;++j) {
            const double dot=rows[i].x*rows[j].x+rows[i].y*rows[j].y+rows[i].z*rows[j].z;
            if(std::abs(dot-(i==j?1.0:0.0))>.02)return false;
        }
    }
    const double determinant=rows[0].x*(rows[1].y*rows[2].z-rows[1].z*rows[2].y)
        -rows[0].y*(rows[1].x*rows[2].z-rows[1].z*rows[2].x)
        +rows[0].z*(rows[1].x*rows[2].y-rows[1].y*rows[2].x);
    return std::abs(determinant-1.0)<=.03;
}
void vector_json(std::ostringstream& o,const TelemVect3& p) {
    o<<'[';number(o,p.x);o<<',';number(o,p.y);o<<',';number(o,p.z);o<<']';
}
// Preserve native coordinates, not inferred sides. SDK local axes are +x left,
// +y up, +z rear; mOri rows convert local vectors into world coordinates.
// Empty / partially observed arrays must never become an inferred clear.
void traffic_json(std::ostringstream& o,Source* s,const TelemInfoV01* player) {
    auto& c=s->copy;
    const auto unavailable=[&]() {
        o<<"{\"status\":\"UNAVAILABLE\",\"player\":null,\"vehicles\":[],\"source\":\"TELEMETRY\",\"complete\":false}";
    };
    const auto nt=c.telemetry.activeVehicles;
    const auto ns=c.scoring.scoringInfo.mNumVehicles;
    const double age=player?player->mElapsedTime-c.scoring.scoringInfo.mCurrentET:0;
    if(!player || !s->telemetry || !s->scoring || nt<1 || nt>104 || ns<1 || ns>104 ||
       !std::isfinite(player->mElapsedTime) || !finite_position(player->mPos) ||
       !valid_orientation(player->mOri) || !std::isfinite(age) || age<-.25 || age>2.0) {
        unavailable();return;
    }
    for(unsigned i=0;i<nt;++i) {
        if(c.telemetry.telemInfo[i].mID<0) { unavailable();return; }
        for(unsigned j=0;j<i;++j)if(c.telemetry.telemInfo[i].mID==c.telemetry.telemInfo[j].mID) {
            unavailable();return;
        }
    }
    const VehicleScoringInfoV01* scoredPlayer=nullptr;
    for(long i=0;i<ns;++i) {
        const auto& v=c.scoring.vehScoringInfo[i];
        if(v.mID<0) { unavailable();return; }
        for(long j=0;j<i;++j)if(v.mID==c.scoring.vehScoringInfo[j].mID) { unavailable();return; }
        if(v.mIsPlayer) {
            if(scoredPlayer || v.mID!=player->mID) { unavailable();return; }
            scoredPlayer=&v;
        }
    }
    if(!scoredPlayer) { unavailable();return; }
    bool complete=true,first=true;
    std::ostringstream vehicles;
    // Join by ID, never by array position: multiplayer may reuse slots.
    for(long i=0;i<ns;++i) {
        const auto& v=c.scoring.vehScoringInfo[i];
        // Pit/garage vehicles are excluded by this spotter's existing policy.
        // They need not have a telemetry slot (LMU may omit inactive cars).
        // Requiring that slot first would silence every on-track opponent.
        // Missing telemetry for an on-track car still withdraws coverage.
        if(v.mID!=player->mID && v.mInPits)continue;
        const TelemInfoV01* t=nullptr;
        for(unsigned j=0;j<nt;++j)if(c.telemetry.telemInfo[j].mID==v.mID)t=&c.telemetry.telemInfo[j];
        if(!t || !std::isfinite(t->mElapsedTime) || std::abs(player->mElapsedTime-t->mElapsedTime)>.25 ||
           !finite_position(t->mPos)) { complete=false;continue; }
        if(v.mID==player->mID)continue;
        if(v.mControl!=1 && v.mControl!=2) { complete=false;continue; }
        if(v.mInPits)continue;
        if(!first)vehicles<<',';first=false;
        vehicles<<"{\"vehicle_id\":"<<t->mID<<",\"telemetry_et\":";number(vehicles,t->mElapsedTime);
        vehicles<<",\"position_m\":";vector_json(vehicles,t->mPos);
        vehicles<<",\"in_pits\":false,\"control_code\":"<<static_cast<int>(v.mControl)<<'}';
    }
    // Unmatched telemetry can be a newly joined car not yet in scoring.
    for(unsigned i=0;i<nt;++i) {
        bool found=false;
        for(long j=0;j<ns;++j)if(c.telemetry.telemInfo[i].mID==c.scoring.vehScoringInfo[j].mID)found=true;
        if(!found)complete=false;
    }
    o<<"{\"status\":\"AVAILABLE\",\"player\":{\"vehicle_id\":"<<player->mID<<",\"telemetry_et\":";
    number(o,player->mElapsedTime);o<<",\"position_m\":";vector_json(o,player->mPos);
    o<<",\"orientation\":[";
    for(unsigned i=0;i<3;++i) { if(i)o<<',';vector_json(o,player->mOri[i]); }
    o<<"]},\"vehicles\":["<<vehicles.str()<<"],\"source\":\"TELEMETRY\",\"complete\":"<<(complete?"true":"false")<<'}';
}
unsigned class_key(const char* value, size_t size) {
    if(!value[0])return 0;
    unsigned hash=2166136261u;
    for(size_t i=0;i<size && value[i];++i) { hash^=static_cast<unsigned char>(value[i]);hash*=16777619u; }
    return (hash&0x7fffffffu)+1u;
}
void tyre_wet_json(std::ostringstream& o,const char* value,size_t size) {
    std::string text;
    for(size_t i=0;i<size && value[i];++i) {
        char c=value[i];text+=c>='A'&&c<='Z'?char(c-'A'+'a'):c;
    }
    if(text.find("wet")!=std::string::npos || text.find("rain")!=std::string::npos)o<<"true";
    else if(text.find("slick")!=std::string::npos || text=="soft" || text=="medium" || text=="hard")o<<"false";
    else o<<"null";
}
void scoring_json(std::ostringstream& o,Source* s,bool aligned) {
    auto& c=s->copy;auto& si=c.scoring.scoringInfo;
    if(!aligned || si.mNumVehicles<0 || si.mNumVehicles>104) { o<<"null";return; }
    for(long i=0;i<si.mNumVehicles;++i) {
        if(c.scoring.vehScoringInfo[i].mID<0) { o<<"null";return; }
        for(long j=0;j<i;++j)if(c.scoring.vehScoringInfo[i].mID==c.scoring.vehScoringInfo[j].mID) { o<<"null";return; }
    }
    o<<'[';
    for(long i=0;i<si.mNumVehicles;++i) {
        if(i)o<<',';
        auto& row=c.scoring.vehScoringInfo[i];
#define SCORE(KEY, VALID, VALUE) o<<",\"" KEY "\":";if(VALID)number(o,VALUE);else o<<"null"
        o<<"{\"vehicle_id\":"<<row.mID;
        unsigned cls=class_key(row.mVehicleClass,sizeof(row.mVehicleClass));
        SCORE("class_id",cls,cls);
        int place=1;bool valid=cls && row.mPlace>0;
        for(long j=0;j<si.mNumVehicles;++j) {
            auto& other=c.scoring.vehScoringInfo[j];
            if(class_key(other.mVehicleClass,sizeof(other.mVehicleClass))!=cls)continue;
            if(!other.mPlace || (j!=i && other.mPlace==row.mPlace))valid=false;
            if(other.mPlace<row.mPlace)++place;
        }
        SCORE("class_position",valid,place);
        SCORE("overall_position",row.mPlace>0,static_cast<unsigned>(row.mPlace));
        SCORE("total_laps",row.mTotalLaps>=0,row.mTotalLaps);
        SCORE("lap_distance_m",true,row.mLapDist);
        SCORE("last_lap_time",row.mLastLapTime>0,row.mLastLapTime);
        SCORE("best_lap_time",row.mBestLapTime>0,row.mBestLapTime);
        SCORE("lap_start_et",row.mLapStartET>=0,row.mLapStartET);
        SCORE("speed_mps",true,std::hypot(row.mLocalVel.x,row.mLocalVel.y,row.mLocalVel.z));
        SCORE("path_lateral_m",true,row.mPathLateral);
        SCORE("track_edge_m",true,row.mTrackEdge);
        SCORE("time_behind_next_s",row.mTimeBehindNext>=0,row.mTimeBehindNext);
        SCORE("laps_behind_next",row.mLapsBehindNext>=0,row.mLapsBehindNext);
        SCORE("time_behind_leader_s",row.mTimeBehindLeader>=0,row.mTimeBehindLeader);
        SCORE("laps_behind_leader",row.mLapsBehindLeader>=0,row.mLapsBehindLeader);
        SCORE("pit_stops",row.mNumPitstops>=0,row.mNumPitstops);
        SCORE("finish_status",true,static_cast<int>(row.mFinishStatus));
        SCORE("control_code",true,static_cast<int>(row.mControl));
        SCORE("sector",row.mSector>=0 && row.mSector<=2,static_cast<int>(row.mSector));
        o<<",\"in_pits\":"<<(row.mInPits?"true":"false")<<'}';
#undef SCORE
    }
    o<<']';
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
    // Vehicle/track labels only. Never expose driver names, Steam IDs or setup.
    o<<",\"car_name\":";if(t)text_json(o,t->mVehicleName,sizeof(t->mVehicleName));else o<<"null";
    o<<",\"vehicle_model\":";if(t)text_json(o,t->mVehicleModel,sizeof(t->mVehicleModel));else o<<"null";
    o<<",\"track_name\":";if(t)text_json(o,t->mTrackName,sizeof(t->mTrackName));else o<<"null";
    NUM("scoring_et",s->scoring,si.mCurrentET);
    NUM("lap_start_et",t,t->mLapStartET);
    NUM("scoring_lap_start_et",v,v->mLapStartET);
    NUM("total_laps",v,v->mTotalLaps);
    NUM("last_lap_time",v,v->mLastLapTime);
    NUM("best_lap_time",v && v->mBestLapTime>0,v->mBestLapTime);
    NUM("outstanding_penalties",v && v->mNumPenalties>=0,static_cast<int>(v->mNumPenalties));
    NUM("fuel_l",t && t->mFuel>=0,t->mFuel);
    NUM("fuel_capacity_l",t && t->mFuelCapacity>0,t->mFuelCapacity);
    NUM("lap_distance_m",v,v->mLapDist);
    NUM("track_length_m",aligned && si.mLapDist>0,si.mLapDist);
    NUM("session_type_code",aligned,si.mSession);
    NUM("session_phase",aligned,static_cast<unsigned>(si.mGamePhase));
    NUM("session_remaining_s",aligned && si.mSessionTimeRemaining>=0,si.mSessionTimeRemaining);
    NUM("session_end_et",aligned && si.mEndET>=0,si.mEndET);
    NUM("session_max_laps",aligned && si.mMaxLaps>0 && si.mMaxLaps<2147483647,si.mMaxLaps);
    NUM("overall_position",v && v->mPlace>0,static_cast<unsigned>(v->mPlace));
    // Class order is derived from the SDK's scored overall order, never from
    // timing estimates. Missing/duplicate places make the result unavailable.
    int classPosition=0,classCars=0;bool classOrderValid=v && v->mPlace>0 && v->mVehicleClass[0];
    bool occupied[256]={};
    if(classOrderValid) for(long i=0;i<si.mNumVehicles;++i) {
        const auto& other=c.scoring.vehScoringInfo[i];
        if(std::strncmp(other.mVehicleClass,v->mVehicleClass,sizeof(v->mVehicleClass))!=0)continue;
        ++classCars;
        if(!other.mPlace || occupied[other.mPlace])classOrderValid=false;
        occupied[other.mPlace]=true;
        if(other.mPlace<=v->mPlace)++classPosition;
    }
    NUM("class_position",classOrderValid,classPosition);
    NUM("class_vehicle_count",classOrderValid,classCars);
    NUM("vehicle_count",aligned,si.mNumVehicles);
    NUM("control_code",v,static_cast<int>(v->mControl));
    NUM("primary_flag",v,static_cast<unsigned>(v->mFlag));
    NUM("yellow_flag_state",aligned,static_cast<int>(si.mYellowFlagState));
    // mFlag only reports green/blue. Local yellows are the independent SDK
    // sector array; keep its source order instead of inventing a track mapping.
    o<<",\"sector_flags\":";
    if(aligned) {
        o<<'[';
        for(unsigned i=0;i<3;++i) {
            if(i)o<<',';
            const int flag=static_cast<int>(si.mSectorFlag[i]);
            if(flag>=-1 && flag<=7)o<<flag;else o<<"null";
        }
        o<<']';
    } else o<<"null";
    NUM("sector",v && v->mSector>=0 && v->mSector<=2,static_cast<int>(v->mSector));
    NUM("pit_state",v,static_cast<unsigned>(v->mPitState));
    NUM("finish_status",v,static_cast<int>(v->mFinishStatus));
    BOOL("under_yellow",v,v->mUnderYellow);
    BOOL("in_garage_stall",v,v->mInGarageStall);
    NUM("rain_ratio",aligned && si.mRaining>=0 && si.mRaining<=1,si.mRaining);
    NUM("track_wetness_ratio",aligned && si.mAvgPathWetness>=0 && si.mAvgPathWetness<=1,si.mAvgPathWetness);
    NUM("ambient_temp_c",aligned && si.mAmbientTemp>-273.15,si.mAmbientTemp);
    NUM("track_temp_c",aligned && si.mTrackTemp>-273.15,si.mTrackTemp);
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
    NUM("gear",t && t->mGear>=-1,t->mGear);
    NUM("engine_rpm",t && t->mEngineRPM>=0,t->mEngineRPM);
    NUM("engine_water_temp_c",t && t->mEngineWaterTemp>-273.15,t->mEngineWaterTemp);
    NUM("engine_oil_temp_c",t && t->mEngineOilTemp>-273.15,t->mEngineOilTemp);
    BOOL("overheating",t,t->mOverheating);
    BOOL("parts_detached",t,t->mDetached);
    NUM("last_impact_et",t && t->mLastImpactET>0,t->mLastImpactET);
    NUM("last_impact_magnitude",t && t->mLastImpactMagnitude>=0,t->mLastImpactMagnitude);
    NUM("yaw_rate",t,t->mLocalRot.y);
    NUM("steering_rad",t && t->mPhysicalSteeringWheelRange>0,
        t->mUnfilteredSteering*t->mPhysicalSteeringWheelRange*0.008726646259971648);
    NUM("brake_bias",t && t->mRearBrakeBias>=0 && t->mRearBrakeBias<=1,100.*(1.-t->mRearBrakeBias));
    NUM("tc_setting",t && t->mTCMax>0 && t->mTC<=t->mTCMax,static_cast<unsigned>(t->mTC));
    NUM("abs_setting",t && t->mABSMax>0 && t->mABS<=t->mABSMax,static_cast<unsigned>(t->mABS));
    NUM("front_tyre_compound",t,static_cast<unsigned>(t->mFrontTireCompoundIndex));
    NUM("rear_tyre_compound",t,static_cast<unsigned>(t->mRearTireCompoundIndex));
    o<<",\"front_tyre_wet\":";if(t)tyre_wet_json(o,t->mFrontTireCompoundName,sizeof(t->mFrontTireCompoundName));else o<<"null";
    o<<",\"rear_tyre_wet\":";if(t)tyre_wet_json(o,t->mRearTireCompoundName,sizeof(t->mRearTireCompoundName));else o<<"null";
    NUM("class_id",v && v->mVehicleClass[0],class_key(v->mVehicleClass,sizeof(v->mVehicleClass)));
    NUM("pit_box_lap_distance_m",v && v->mPitLapDist>0 && si.mLapDist>v->mPitLapDist,v->mPitLapDist);
    NUM("time_gap_ahead_s",t && t->mTimeGapCarAhead>0,t->mTimeGapCarAhead);
    NUM("time_gap_behind_s",t && t->mTimeGapCarBehind>0,t->mTimeGapCarBehind);
    NUM("place_gap_ahead_s",t && t->mTimeGapPlaceAhead>0,t->mTimeGapPlaceAhead);
    NUM("place_gap_behind_s",t && t->mTimeGapPlaceBehind>0,t->mTimeGapPlaceBehind);
    NUM("start_light",aligned,static_cast<unsigned>(si.mStartLight));
    NUM("start_red_lights",aligned,static_cast<unsigned>(si.mNumRedLights));
    NUM("track_limits_steps_per_penalty",aligned && si.mTrackLimitsStepsPerPenalty>0,static_cast<unsigned>(si.mTrackLimitsStepsPerPenalty));
    NUM("clutch_ratio",t && t->mUnfilteredClutch>=0 && t->mUnfilteredClutch<=1,t->mUnfilteredClutch);
    BOOL("abs_active",t,t->mABSActive);
    BOOL("tc_active",t,t->mTCActive);
    BOOL("speed_limiter_active",t,t->mSpeedLimiterActive);
    // The SDK orders wheels FL/FR/RL/RR. Temperatures are left/center/right
    // across each tyre, not inner/center/outer. Keep unobserved values null.
    const char* wheelPositions[]={"FL","FR","RL","RR"};
    o << ",\"wheels\":[";
    for(unsigned wheel=0;wheel<4;++wheel) {
        if(wheel)o<<',';
        o<<"{\"position\":\""<<wheelPositions[wheel]<<'"';
        NUM("pressure_kpa",t && t->mWheel[wheel].mPressure>=0,t->mWheel[wheel].mPressure);
        NUM("brake_temp_c",t && t->mWheel[wheel].mBrakeTemp>-273.15,t->mWheel[wheel].mBrakeTemp);
        NUM("suspension_deflection_m",t,t->mWheel[wheel].mSuspensionDeflection);
        NUM("ride_height_m",t,t->mWheel[wheel].mRideHeight);
        NUM("wear_fraction",t && t->mWheel[wheel].mWear>=0 && t->mWheel[wheel].mWear<=1,t->mWheel[wheel].mWear);
        BOOL("flat",t,t->mWheel[wheel].mFlat);
        BOOL("detached",t,t->mWheel[wheel].mDetached);
        o<<",\"temperature_c\":[";
        for(unsigned point=0;point<3;++point) {
            if(point)o<<',';
            if(t && t->mWheel[wheel].mTemperature[point]>0)
                number(o,t->mWheel[wheel].mTemperature[point]-273.15);
            else o<<"null";
        }
        o<<"]}";
    }
    o<<']';
    o<<",\"traffic\":";traffic_json(o,s,t);
    o<<",\"scoring_cars\":";scoring_json(o,s,aligned);
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
