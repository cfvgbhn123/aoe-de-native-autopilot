// Exercise the actual bridge recovery and locking code with a fake offline world.
#define AOE_BUILD_ID 0u
#include "../native/aoe_native_bridge.cpp"
#include <assert.h>
static Shared state={};
static BYTE gameData[0xcb0]={},commData[24]={},worldData[0x200]={},playerData[0x400]={};
static uintptr_t players[2]={};
static float balances[4]={};
static BYTE aiData[0x12d08]={};
static HANDLE entered,proceed;
static volatile LONG releases=0,destructions=0;
static volatile LONG cancellations=0;
static BYTE fakeVisibility=0;
static uintptr_t expectedResource=0;
static BYTE fakeVisible(uintptr_t,int x,int y) {assert(x==1 && y==1);return fakeVisibility;}
static int fakeSeen(uintptr_t info,uintptr_t u,int flags) {
    assert(info==ownedAI+0x938 && u==expectedResource && flags==0);
    at<int>(info,0x49c)++;return 1;
}
static uintptr_t fakeDestroy(uintptr_t,unsigned) {InterlockedIncrement(&destructions);return 0;}
static int fakeCancel(uintptr_t,int preserve) {assert(preserve==0);InterlockedIncrement(&cancellations);return 1;}
static void updateBody(uintptr_t) {SetEvent(entered);WaitForSingleObject(proceed,3000);}
static DWORD WINAPI updateThread(void*) {hookedUpdate(player);return 0;}
static DWORD WINAPI releaseThread(void*) {DWORD ok=AoeRelease(nullptr);InterlockedExchange(&releases,static_cast<LONG>(ok));return 0;}
static void fixture() {
    state={};shared=&state;bound=true;cleanupPending=false;ownedAI=0;oldType=1;oldComputerFlag=0;
    world=reinterpret_cast<uintptr_t>(worldData);player=reinterpret_cast<uintptr_t>(playerData);
    scenario=0x1234;playerId=1;lastTime=100;players[1]=player;
    at<uintptr_t>(base,0xf96b48)=reinterpret_cast<uintptr_t>(gameData);
    at<uintptr_t>(base,0xf96ab8)=reinterpret_cast<uintptr_t>(commData);
    at<int>(reinterpret_cast<uintptr_t>(commData),0xc)=4;
    at<uintptr_t>(reinterpret_cast<uintptr_t>(gameData),0x660)=world;
    at<int>(reinterpret_cast<uintptr_t>(gameData),0xc14)=-1;
    at<int>(reinterpret_cast<uintptr_t>(gameData),0xc48)=1;
    at<uintptr_t>(world,0x40)=reinterpret_cast<uintptr_t>(players);
    at<uintptr_t>(world,0x60)=scenario;at<int>(world,0x70)=2;at<int>(world,0x7c)=100;
    at<BYTE>(player,0x68)=1;at<short>(player,0x6a)=1;at<BYTE>(player,0xc0)=0;
    at<int>(player,0x354)=0;at<uintptr_t>(player,0x18)=0;
    at<uintptr_t>(player,0x3c0)=0;at<uintptr_t>(player,0x80)=reinterpret_cast<uintptr_t>(balances);
    shared->h[Heartbeat]=GetTickCount();shared->h[20]=1;
}
int main() {
    base=reinterpret_cast<uintptr_t>(VirtualAlloc(nullptr,0x1000000,MEM_COMMIT|MEM_RESERVE,PAGE_EXECUTE_READWRITE));
    assert(base);
    // Destructor trampoline lets the production cleanup path run in the fixture.
    BYTE thunk[12]={0x48,0xb8};uintptr_t target=reinterpret_cast<uintptr_t>(fakeDestroy);
    memcpy(thunk+2,&target,8);thunk[10]=0xff;thunk[11]=0xe0;
    memcpy(reinterpret_cast<void*>(base+0x2c4450),thunk,12);
    at<uintptr_t>(base+0xbb5860,8)=base+0x2c4450;
    fixture();assert(identity(game()));
    at<int>(world,0x7c)=99;assert(!identity(game()));
    fixture();at<uintptr_t>(reinterpret_cast<uintptr_t>(gameData),0x660)=0;assert(!identity(game()));
    fixture();at<uintptr_t>(world,0x60)=0x5678;assert(!identity(game()));
    fixture();players[1]=0xdeadbeef;assert(!identity(game()));
    fixture();at<int>(reinterpret_cast<uintptr_t>(gameData),0xc48)=7;assert(!identity(game()));
    fixture();at<uintptr_t>(player,0x3c0)=0xdeadbeef;at<BYTE>(player,0x68)=3;
    assert(!release());assert(at<uintptr_t>(player,0x3c0)==0xdeadbeef);
    fixture();ownedAI=reinterpret_cast<uintptr_t>(aiData);at<uintptr_t>(ownedAI)=base+0xbb5860;
    at<uintptr_t>(player,0x3c0)=ownedAI;at<BYTE>(player,0x68)=3;at<BYTE>(player,0xc0)=2;
    assert(release());assert(at<BYTE>(player,0xc0)==2);assert(at<uintptr_t>(player,0x3c0)==0);
    disposeAI();assert(destructions==1 && !ownedAI && !cleanupPending);
    fixture();ownedAI=reinterpret_cast<uintptr_t>(aiData);at<uintptr_t>(ownedAI)=base+0xbb5860;
    oldComputerFlag=4;at<int>(player,0x354)=1;at<uintptr_t>(player,0x3c0)=ownedAI;at<BYTE>(player,0x68)=3;
    assert(release());assert(at<int>(player,0x354)==4);
    ownedAI=0;cleanupPending=false;
    fixture();at<uintptr_t>(player,0x3c0)=0xdeadbeef;at<int>(player,0x354)=9;
    assert(!release());assert(at<int>(player,0x354)==9);
    // Narrow healing predicate invokes engine cancellation, never mutates actions.
    fixture();BYTE unit[0x300]={},type[0xb0]={},unitAI[0x100]={},action[0x100]={},actionList[0x30]={},list[0x10]={};
    uintptr_t u=reinterpret_cast<uintptr_t>(unit),t=reinterpret_cast<uintptr_t>(type);
    uintptr_t ua=reinterpret_cast<uintptr_t>(unitAI),a=reinterpret_cast<uintptr_t>(action);
    uintptr_t al=reinterpret_cast<uintptr_t>(actionList),ul=reinterpret_cast<uintptr_t>(list);
    uintptr_t node[2]={u,0},entries[1]={a};
    at<uintptr_t>(player,0x18)=ul;at<uintptr_t>(ul,8)=reinterpret_cast<uintptr_t>(node);
    at<uintptr_t>(u,0x18)=t;at<short>(t,0x32)=4;at<BYTE>(u,0x80)=2;
    at<uintptr_t>(u,0xd8)=ua;at<uintptr_t>(u,0x278)=al;at<uintptr_t>(ua)=base+0xbc0200;
    at<uintptr_t>(al,8)=u;at<uintptr_t>(al,0x10)=reinterpret_cast<uintptr_t>(entries);at<uint64_t>(al,0x20)=1;
    at<uintptr_t>(a,0x10)=u;at<short>(a,8)=101;at<BYTE>(a,0xa)=16;
    target=reinterpret_cast<uintptr_t>(fakeCancel);memcpy(thunk+2,&target,8);memcpy(reinterpret_cast<void*>(base+0xbeaa0),thunk,12);
    at<uintptr_t>(base+0xbc0200,0xa8)=base+0xbeaa0;
    repairStalledBuilders();assert(cancellations==1 && shared->h[22]==1);
    at<short>(a,0x60)=1;repairStalledBuilders();assert(cancellations==1);
    at<short>(a,0x60)=0;at<uintptr_t>(a,0x18)=u;repairStalledBuilders();assert(cancellations==1);
    at<uintptr_t>(a,0x18)=0;at<BYTE>(a,0xa)=7;repairStalledBuilders();assert(cancellations==1);
    at<BYTE>(a,0xa)=16;at<short>(a,8)=110;repairStalledBuilders();assert(cancellations==1);
    // Historical resource replay respects fog, static-object flags and bounds.
    fixture();BYTE gaiaData[0x40]={},visibleData[0x40]={};
    uintptr_t gaia=reinterpret_cast<uintptr_t>(gaiaData),visible=reinterpret_cast<uintptr_t>(visibleData);
    players[0]=gaia;at<uintptr_t>(gaia,0x18)=ul;at<uintptr_t>(player,0x38)=visible;
    at<uintptr_t>(visible,0x18)=player;at<int>(visible,0x28)=3;at<int>(visible,0x2c)=3;
    at<short>(t,0x32)=15;at<BYTE>(u,0x80)=3;at<float>(u,0x5c)=1.5f;at<float>(u,0x60)=1.5f;
    ownedAI=reinterpret_cast<uintptr_t>(aiData);expectedResource=u;
    target=reinterpret_cast<uintptr_t>(fakeVisible);memcpy(thunk+2,&target,8);memcpy(reinterpret_cast<void*>(base+0x353980),thunk,12);
    target=reinterpret_cast<uintptr_t>(fakeSeen);memcpy(thunk+2,&target,8);memcpy(reinterpret_cast<void*>(base+0x2b5bd0),thunk,12);
    fakeVisibility=0;seedKnownResources();assert(shared->h[23]==0);
    fakeVisibility=0x80;seedKnownResources();assert(shared->h[23]==0);
    at<BYTE>(t,0xa1)=1;seedKnownResources();assert(shared->h[23]==1 && shared->h[25]==1);
    at<BYTE>(t,0xa1)=0;fakeVisibility=0xf;seedKnownResources();assert(shared->h[23]==1 && shared->h[25]==2);
    at<float>(u,0x5c)=3;seedKnownResources();assert(shared->h[23]==0);
    at<float>(u,0x5c)=1.5f;at<short>(t,0x32)=21;seedKnownResources();assert(shared->h[23]==0);
    ownedAI=0;players[0]=0;
    fixture();shared->h[Sequence]=17;shared->h[Ack]=16;shared->h[Command]=1;
    assert(AoeRelease(nullptr)==1);assert(shared->h[Ack]==17 && shared->h[Command]==2);
    fixture();ownedAI=reinterpret_cast<uintptr_t>(aiData);at<uintptr_t>(ownedAI)=base+0xbb5860;
    at<uintptr_t>(player,0x3c0)=ownedAI;at<BYTE>(player,0x68)=3;shared->h[Enabled]=1;
    entered=CreateEventW(nullptr,TRUE,FALSE,nullptr);proceed=CreateEventW(nullptr,TRUE,FALSE,nullptr);
    original=updateBody;HANDLE update=CreateThread(nullptr,0,updateThread,nullptr,0,nullptr);
    assert(WaitForSingleObject(entered,2000)==WAIT_OBJECT_0);
    HANDLE restore=CreateThread(nullptr,0,releaseThread,nullptr,0,nullptr);
    // Release must wait until the original AI tick relinquishes its reference.
    assert(WaitForSingleObject(restore,100)==WAIT_TIMEOUT);assert(releases==0);
    assert(at<uintptr_t>(player,0x3c0)==ownedAI);
    SetEvent(proceed);assert(WaitForSingleObject(update,2000)==WAIT_OBJECT_0);
    assert(WaitForSingleObject(restore,2000)==WAIT_OBJECT_0);assert(releases==1);
    assert(at<BYTE>(player,0x68)==1 && at<uintptr_t>(player,0x3c0)==0);
    assert(at<BYTE>(player,0xc0)==0);disposeAI();assert(destructions==2);
    CloseHandle(update);CloseHandle(restore);CloseHandle(entered);CloseHandle(proceed);
    VirtualFree(reinterpret_cast<void*>(base),0,MEM_RELEASE);
    puts("Native flag restoration, narrow builder healing, recovery, foreign-AI rejection, rewind, destructor and concurrent release: passed.");
    return 0;
}
