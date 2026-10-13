// AoE I DE Steam build 97381 x64. Commands execute on the simulation thread.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>


constexpr uint32_t MAGIC=0x414f4541, VERSION=6, BYTES=4096;
enum H { Magic,Version,Size,Installed,Sequence,Command,Ack,Result,Enabled,Heartbeat,
    Owner,PlayerId,Time,Updates,Units,Villagers,Buildings,Stage,Snapshot };
struct Shared {
    volatile uint32_t h[32];
    uint64_t world,player,scenario,ai;
    float resources[4];
    char build[256],personality[256],message[256];
};
static Shared* shared=nullptr;
static HANDLE mapping=nullptr;
static uintptr_t base=0,world=0,player=0,scenario=0,ownedAI=0;
static BYTE oldType=1;
static int oldComputerFlag=0;
static bool bound=false, cleanupPending=false;
static int playerId=0;
static HANDLE watchdog=nullptr;
static int lastTime=0;
using Update=void(*)(uintptr_t);
static Update original=nullptr;
static SRWLOCK controlLock=SRWLOCK_INIT;
template<class T> static T& at(uintptr_t p,size_t o=0) {return *reinterpret_cast<T*>(p+o);}
static uintptr_t game() {return at<uintptr_t>(base,0xf96b48);}
static bool offline(uintptr_t g) {
    uintptr_t c=at<uintptr_t>(base,0xf96ab8);
    return g && c && at<int>(c,0xc)==4 && !at<BYTE>(c,0x14)
        && !at<BYTE>(g,0xc10) && !at<BYTE>(g,0xc11)
        && at<int>(g,0xc14)==-1 && (at<int>(g,0xc48)==0 || at<int>(g,0xc48)==1);
}
static bool identity(uintptr_t g) {
    if(!bound || !offline(g) || at<uintptr_t>(g,0x660)!=world) return false;
    int n=at<int>(world,0x70),id=playerId;
    return n>=2 && n<=9 && id>0 && id<n
        && at<uintptr_t>(at<uintptr_t>(world,0x40),id*8)==player
        && at<uintptr_t>(world,0x60)==scenario
        && at<short>(player,0x6a)==id && at<int>(world,0x7c)>=lastTime;
}
static void message(const char* text) {strncpy_s(shared->message,text,_TRUNCATE);}
static bool release() {
    uintptr_t current=at<uintptr_t>(player,0x3c0);
    if(current && current!=ownedAI) {
        message("Unowned/loaded AI: manual recovery refused.");return false;
    }
    if(current==ownedAI && ownedAI) {
        // Never write +0xc0 (victory/defeat state).
        at<uintptr_t>(player,0x3c0)=0;
        at<BYTE>(player,0x68)=oldType;
        at<int>(player,0x354)=oldComputerFlag;
        cleanupPending=true;
    }
    bool ok=at<uintptr_t>(player,0x3c0)==0 && at<BYTE>(player,0x68)==1;
    if(ok) {shared->h[Enabled]=0;shared->ai=0;}
    shared->h[21]=cleanupPending?1:0;
    return ok;
}
static void disposeAI() {
    // Only called on the simulation thread, in the still-valid world.
    // vtable +8 is the observed scalar deleting destructor, NOT slot 0.
    if(!ownedAI || !cleanupPending)return;
    if(at<uintptr_t>(ownedAI)!=base+0xbb5860 ||
       at<uintptr_t>(base+0xbb5860,8)!=base+0x2c4450) {
        message("AI destructor signature changed.");return;
    }
    uintptr_t retired=ownedAI;ownedAI=0;cleanupPending=false;shared->h[21]=0;
    using Destroy=uintptr_t(*)(uintptr_t,unsigned);
    reinterpret_cast<Destroy>(base+0x2c4450)(retired,1);
}
static void invalidate() {
    // Do not dereference old pointers after a load/world replacement.
    // An AI still attached belongs to engine teardown. A detached pending
    // object cannot safely be destructed using a replaced world.
    bound=false;ownedAI=0;cleanupPending=false;
    shared->h[Enabled]=0;shared->h[20]=0;shared->h[21]=0;shared->ai=0;
    shared->h[Result]=4;shared->h[Command]=0;
    MemoryBarrier();shared->h[Ack]=shared->h[Sequence];
    message("Match changed or time rewound; reconnect required.");
}
static void enumerate(uintptr_t p, bool registerAI) {
    uint32_t units=0,villagers=0,buildings=0;
    uintptr_t list=at<uintptr_t>(p,0x18);
    uintptr_t node=list?at<uintptr_t>(list,8):0;
    for(unsigned guard=0;node && guard<100000;guard++) {
        uintptr_t u=at<uintptr_t>(node),t=at<uintptr_t>(u,0x18);
        if(at<BYTE>(u,0x80)==2) {
            units++;
            short id=at<short>(t,0x2c),kind=at<short>(t,0x32);
            if(kind==4 || id==83 || id==293) villagers++;
            if(kind==3) buildings++;
        }
        if(registerAI && at<BYTE>(u,0x80)==2) {
            using Add=int(*)(uintptr_t,uintptr_t);
            reinterpret_cast<Add>(base+0x2c4490)(ownedAI,u);
            using AddInfo=void(*)(uintptr_t,uintptr_t,int);
            reinterpret_cast<AddInfo>(base+0x2b18a0)(ownedAI+0x50,u,1);
        }
        node=at<uintptr_t>(node,8);
    }
    shared->h[Units]=units;shared->h[Villagers]=villagers;shared->h[Buildings]=buildings;
}
static void seedKnownResources() {
    // Recreating InfoAI loses historical discovery notifications. Replay only
    // already-revealed Gaia resources using the native visibility/seen-object
    // paths, including the native static-object rule for explored fog tiles.
    uintptr_t visibility=at<uintptr_t>(player,0x38);
    if(!visibility || at<uintptr_t>(visibility,0x18)!=player)return;
    int width=at<int>(visibility,0x28),height=at<int>(visibility,0x2c);
    if(width<=0 || height<=0 || width>512 || height>512)return;
    uintptr_t gaia=at<uintptr_t>(at<uintptr_t>(world,0x40));
    uintptr_t list=gaia?at<uintptr_t>(gaia,0x18):0;
    uintptr_t node=list?at<uintptr_t>(list,8):0;
    using Visible=BYTE(*)(uintptr_t,int,int);
    using Seen=int(*)(uintptr_t,uintptr_t,int);
    uint32_t seeded=0;
    for(unsigned guard=0;node && guard<100000;guard++) {
        uintptr_t u=at<uintptr_t>(node),t=at<uintptr_t>(u,0x18);
        int kind=at<short>(t,0x32);
        BYTE state=at<BYTE>(u,0x80);
        if(kind>=0 && kind<=33 && (0x3800087a0ULL&(1ULL<<kind)) && (state==2 || state==3)) {
            float xf=at<float>(u,0x5c),yf=at<float>(u,0x60);
            if(xf>=0 && yf>=0 && xf<static_cast<float>(width) && yf<static_cast<float>(height)) {
                BYTE seen=reinterpret_cast<Visible>(base+0x353980)(visibility,static_cast<int>(xf),static_cast<int>(yf));
                if(seen && (seen!=0x80 || at<BYTE>(t,0xa1))) {
                    if(reinterpret_cast<Seen>(base+0x2b5bd0)(ownedAI+0x938,u,0))seeded++;
                }
            }
        }
        node=at<uintptr_t>(node,8);
    }
    shared->h[23]=seeded;
    for(int r=0;r<4;r++)shared->h[24+r]=static_cast<uint32_t>(at<int>(ownedAI+0x938,0x498+r*4));
}
static bool enable(uintptr_t g) {
    if(shared->h[Enabled] && ownedAI && at<uintptr_t>(player,0x3c0)==ownedAI)return true;
    if(ownedAI) {message("Previous AI cleanup has not completed.");return false;}
    if(at<BYTE>(player,0x68)!=1 || at<uintptr_t>(player,0x3c0)) {
        message("Player is not a human without AI.");return false;
    }
    if(at<BYTE>(player,0xc0)!=0) {message("Player is no longer active.");return false;}
    oldType=at<BYTE>(player,0x68);
    oldComputerFlag=at<int>(player,0x354);
    float resourcesBefore[4];
    memcpy(resourcesBefore,reinterpret_cast<void*>(at<uintptr_t>(player,0x80)),16);
    shared->h[Stage]=1;
    using Allocate=void*(*)(size_t);
    uintptr_t storage=reinterpret_cast<uintptr_t>(reinterpret_cast<Allocate>(base+0xa63354)(0x12d08));
    if(!storage) {message("Engine allocation failed.");return false;}
    using Construct=uintptr_t(*)(uintptr_t,void*,int,const char*,uintptr_t,uintptr_t,uintptr_t,uintptr_t);
    shared->h[Stage]=2;
    ownedAI=reinterpret_cast<Construct>(base+0x2c3fd0)(storage,nullptr,
        at<short>(player,0x6a),reinterpret_cast<const char*>(at<uintptr_t>(player,0x50)),player,0,0,0);
    if(!ownedAI) {message("AI constructor returned null.");return false;}
    at<uintptr_t>(player,0x3c0)=ownedAI;
    at<BYTE>(player,0x68)=3;
    // Native setup sets both fields. +0x354 routes unit completion/failure
    // notifications to computer AI; control type alone leaves villagers stuck.
    at<int>(player,0x354)=1;
    shared->h[Stage]=3;
    enumerate(player,true);
    shared->h[Stage]=4;
    // Use the game's localized Random selector. Native setup selects the
    // civilization/map build list and RANDOM MAP.PER just as at match start.
    char random[256]={};
    using Language=void(*)(uintptr_t,int,char*,int);
    reinterpret_cast<Language>(at<uintptr_t>(at<uintptr_t>(g),0xc8))(g,10107,random,256);
    using Setup=void(*)(uintptr_t,const char*,const char*,const char*,int,int);
    int previous=at<int>(g,0xaa0);at<int>(g,0xaa0)=0;
    __try {
        reinterpret_cast<Setup>(base+0x2fee00)(player,random,random,random,0,at<int>(g,0xc40));
    } __finally {at<int>(g,0xaa0)=previous;}
    shared->h[Stage]=5;
    seedKnownResources();
    // Mid-match takeover preserves the player's existing balance. Native
    // opening bonuses belong to lobby AI creation, not repeated takeovers.
    memcpy(reinterpret_cast<void*>(at<uintptr_t>(player,0x80)),resourcesBefore,16);
    strncpy_s(shared->build,reinterpret_cast<const char*>(ownedAI+0x130),_TRUNCATE);
    strncpy_s(shared->personality,reinterpret_cast<const char*>(ownedAI+0x10df0),_TRUNCATE);
    shared->ai=ownedAI;shared->h[Enabled]=1;message("Native AI enabled; hardest difficulty (0).");
    return true;
}
static void repairStalledBuilders() {
    // Use the engine cancellation path only for the proven permanent wait:
    // Build/state16, no target, retry==0. Never rewrite action states or targets.
    uintptr_t list=at<uintptr_t>(player,0x18);
    uintptr_t node=list?at<uintptr_t>(list,8):0;
    uint32_t repaired=0;
    for(unsigned guard=0;node && guard<100000;guard++) {
        uintptr_t u=at<uintptr_t>(node),t=at<uintptr_t>(u,0x18);
        if(at<BYTE>(u,0x80)==2 && at<short>(t,0x32)==4) {
            uintptr_t ua=at<uintptr_t>(u,0xd8),actions=at<uintptr_t>(u,0x278);
            if(ua && actions && at<uintptr_t>(actions,8)==u && at<uint64_t>(actions,0x20)==1) {
                uintptr_t entries=at<uintptr_t>(actions,0x10);
                uintptr_t a=entries?at<uintptr_t>(entries):0;
                if(a && at<uintptr_t>(a,0x10)==u && at<short>(a,8)==101 &&
                   at<BYTE>(a,0xa)==16 && !at<uintptr_t>(a,0x18) && at<short>(a,0x60)==0 &&
                   at<uintptr_t>(ua)==base+0xbc0200 &&
                   at<uintptr_t>(base+0xbc0200,0xa8)==base+0xbeaa0) {
                    using Cancel=int(*)(uintptr_t,int);
                    if(reinterpret_cast<Cancel>(base+0xbeaa0)(ua,0))repaired++;
                }
            }
        }
        node=at<uintptr_t>(node,8);
    }
    shared->h[22]+=repaired;
}
static void work(uintptr_t p) {
    if(!bound)return;
    uintptr_t g=game();
    if(!identity(g)) {invalidate();return;}
    if(p!=player)return;
    lastTime=at<int>(world,0x7c);
    if(shared->h[Enabled] && DWORD(GetTickCount()-shared->h[Heartbeat])>5000) {
        bool ok=release();shared->h[Result]=ok?7:2;
        message(ok?"Controller heartbeat expired; manual control restored.":"Watchdog recovery refused.");
    }
    disposeAI();
    uint32_t seq=shared->h[Sequence];
    if(seq!=shared->h[Ack]) {
        uint32_t cmd=shared->h[Command];bool ok=false;
        if(cmd==1 && DWORD(GetTickCount()-shared->h[Heartbeat])<=5000) {
            ok=enable(g);
            if(ok)repairStalledBuilders();
        }
        else if(cmd==2) {ok=release();if(ok)message("Manual control restored.");}
        shared->h[Result]=ok?0:2;MemoryBarrier();shared->h[Ack]=seq;
    }
}
static void updateLocked(uintptr_t p) {
    __try {work(p);} __except(EXCEPTION_EXECUTE_HANDLER) {
        shared->h[Result]=5;shared->h[Ack]=shared->h[Sequence];
        message("Native bridge exception; recovery attempted.");
        __try {if(identity(game()))release();else invalidate();} __except(EXCEPTION_EXECUTE_HANDLER) {invalidate();}
    }
    uintptr_t g=game();
    bool active=bound && p==player && ownedAI && shared->h[Enabled];
    int previous=active?at<int>(g,0xaa0):0;
    if(active)at<int>(g,0xaa0)=0;
    // The same lock covers original AI updates and the remote release path.
    __try {original(p);} __finally {if(active)at<int>(g,0xaa0)=previous;}
    if(bound && p==player) {
        __try {
            if(identity(g)) {
                shared->h[Snapshot]++;MemoryBarrier();
                __try {
                    shared->h[Updates]++;shared->h[Time]=at<int>(world,0x7c);
                    enumerate(p,false);
                    memcpy(shared->resources,reinterpret_cast<void*>(at<uintptr_t>(p,0x80)),16);
                } __finally {MemoryBarrier();shared->h[Snapshot]++;}
            } else invalidate();
        } __except(EXCEPTION_EXECUTE_HANDLER) {invalidate();}
    }
}
static void hookedUpdate(uintptr_t p) {
    AcquireSRWLockExclusive(&controlLock);
    __try {updateLocked(p);} __finally {ReleaseSRWLockExclusive(&controlLock);}
}
static DWORD WINAPI watch(void*) {
    for(;;) {
        Sleep(100);
        if(!TryAcquireSRWLockExclusive(&controlLock))continue;
        __try {
            __try {
                if(bound && !identity(game()))invalidate();
                if(bound && (shared->h[Enabled] || shared->h[Sequence]!=shared->h[Ack]) &&
                   DWORD(GetTickCount()-shared->h[Heartbeat])>5000) {
                    bool ok=release();shared->h[Command]=2;shared->h[Result]=ok?7:2;
                    MemoryBarrier();shared->h[Ack]=shared->h[Sequence];
                    message(ok?"Controller heartbeat expired; manual control restored.":"Watchdog recovery refused.");
                }
            } __except(EXCEPTION_EXECUTE_HANDLER) {if(shared)invalidate();}
        } __finally {ReleaseSRWLockExclusive(&controlLock);}
    }
}
static DWORD initializeLocked() {
    base=reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    char name[128];sprintf_s(name,"Local\\AoENative_v6_%lu",GetCurrentProcessId());
    if(!mapping)mapping=OpenFileMappingA(FILE_MAP_ALL_ACCESS,FALSE,name);
    if(!mapping)return 0;
    if(!shared)shared=reinterpret_cast<Shared*>(MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,BYTES));
    if(!shared || shared->h[Magic]!=MAGIC || shared->h[Version]!=VERSION || shared->h[Size]!=BYTES)return 0;
    uintptr_t g=game();if(!offline(g))return 0;
    uintptr_t w=at<uintptr_t>(g,0x660),p=shared->player;
    if(!w || at<uintptr_t>(w)!=base+0xbc8958 || shared->world!=w)return 0;
    int count=at<int>(w,0x70),id=static_cast<int>(shared->h[PlayerId]);
    if(count<2 || count>9 || id<=0 || id>=count || at<uintptr_t>(at<uintptr_t>(w,0x40),id*8)!=p)return 0;
    if(at<uintptr_t>(p)!=base+0xbc3630 || at<short>(p,0x6a)!=id || at<uintptr_t>(w,0x60)!=shared->scenario)return 0;
    if(bound && (!identity(g) || world!=w || player!=p || playerId!=id))invalidate();
    if(!bound && (at<BYTE>(p,0x68)!=1 || at<uintptr_t>(p,0x3c0))) {
        message("Unowned/loaded AI: refusing to claim it.");return 0;
    }
    if(memcmp(reinterpret_cast<void*>(base+0x303d00),"\x4c\x8b\xdc\x55\x49\x8d\x6b\xa1",8))return 0;
    uintptr_t slot=base+0xbc3630+0xe0,current=at<uintptr_t>(slot);
    if(current!=base+0x303d00 && current!=reinterpret_cast<uintptr_t>(hookedUpdate))return 0;
    if(!watchdog) {
        // Pin the DLL: its hook and watchdog live until this game process exits.
        HMODULE pinned=nullptr;
        if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,
            reinterpret_cast<LPCWSTR>(hookedUpdate),&pinned))return 0;
        watchdog=CreateThread(nullptr,0,watch,nullptr,0,nullptr);
        if(!watchdog)return 0;
    }
    original=reinterpret_cast<Update>(base+0x303d00);
    DWORD protect;
    if(!VirtualProtect(reinterpret_cast<void*>(slot),8,PAGE_READWRITE,&protect))return 0;
    InterlockedExchangePointer(reinterpret_cast<void*volatile*>(slot),reinterpret_cast<void*>(hookedUpdate));
    DWORD unused;VirtualProtect(reinterpret_cast<void*>(slot),8,protect,&unused);
    world=w;player=p;playerId=id;scenario=at<uintptr_t>(w,0x60);lastTime=at<int>(w,0x7c);
    bound=true;shared->h[20]=1;shared->h[Installed]=1;return 1;
}
extern "C" __declspec(dllexport) DWORD WINAPI AoeBuildId(void*) {return AOE_BUILD_ID;}
extern "C" __declspec(dllexport) DWORD WINAPI AoeInitialize(void*) {
    DWORD result=0;AcquireSRWLockExclusive(&controlLock);
    __try {__try {result=initializeLocked();} __except(EXCEPTION_EXECUTE_HANDLER) {result=0;}}
    __finally {ReleaseSRWLockExclusive(&controlLock);}return result;
}
extern "C" __declspec(dllexport) DWORD WINAPI AoeRelease(void*) {
    DWORD result=0;AcquireSRWLockExclusive(&controlLock);
    __try {
        __try {
            if(shared && identity(game())) {
                bool ok=release();shared->h[Command]=2;shared->h[Result]=ok?0:2;
                MemoryBarrier();shared->h[Ack]=shared->h[Sequence];
                if(ok) {message("Manual control restored; cleanup awaits simulation.");result=1;}
            } else if(shared)invalidate();
        } __except(EXCEPTION_EXECUTE_HANDLER) {result=0;}
    } __finally {ReleaseSRWLockExclusive(&controlLock);}return result;
}
BOOL WINAPI DllMain(HINSTANCE,DWORD,LPVOID) {return TRUE;}
