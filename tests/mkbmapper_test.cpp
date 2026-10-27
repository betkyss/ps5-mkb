// SPDX-License-Identifier: LicenseRef-AGPL-3.0-only-OpenSSL
#include "mkbmapper.h"
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <vector>

#define CHECK(x) do { if(!(x)) { std::cerr << __LINE__ << ": " #x "\n"; std::exit(1); } } while(0)
struct Fixture {
    std::vector<ChiakiControllerState> sent;
    MkbMapper mapper{[this](const ChiakiControllerState &s, const char *){ sent.push_back(s); }};
    uint64_t arm() { CHECK(mapper.arm(true,true,false,1000)); return mapper.generation(); }
    void idle() {
        CHECK(!sent.empty()); auto s=sent.back();
        CHECK(s.buttons==0 && s.l2_state==0 && s.r2_state==0);
        CHECK(s.left_x==0 && s.left_y==0 && s.right_x==0 && s.right_y==0);
        CHECK(s.touches[0].id==-1 && s.touches[1].id==-1);
        CHECK(s.accel_y==1 && s.orient_w==1);
    }
};

static void recoilChecks() {
    auto config=MkbProfile::defaults(); config.recoil_vertical=.02;
    config.binds[97]=MkbMapper::RecoilToggle;
    config.binds[98]=MkbMapper::RecoilDecrease;
    config.binds[100]=MkbMapper::RecoilIncrease;
    ChiakiControllerState state{};
    std::string reason;
    MkbMapper mapper([&](const ChiakiControllerState &s,const char *why){state=s;reason=why;},config);
    CHECK(mapper.arm(true,true,false,1000)); auto g=mapper.generation();
    uint64_t now=1004;
    auto key=[&](MkbMapper::Key k,bool down){mapper.key(g,k,down,now,now);};
    auto control=[&](int source){mapper.physical(g,source,true,now,now);mapper.physical(g,source,false,now,now);};
    key(MkbMapper::L2,true); key(MkbMapper::R2,true); CHECK(state.right_y==0);
    mapper.physical(g,97,true,1004,1004);
    CHECK(state.right_y==655 && reason=="recoil-on-2.0pct");
    mapper.physical(g,97,true,1004,1004,true); CHECK(state.right_y==655);
    mapper.physical(g,97,false,1004,1004);
    key(MkbMapper::R2,false); CHECK(state.right_y==0 && state.l2_state==255);
    key(MkbMapper::R2,true); CHECK(state.right_y==655);
    key(MkbMapper::L2,false); CHECK(state.right_y==0 && state.r2_state==255);
    key(MkbMapper::L2,true); CHECK(state.right_y==655);
    mapper.motion(g,2,-3,1005,1005); mapper.tick(1008);now=1008;
    const auto mouseX=state.right_x; const int mouseY=state.right_y-655;
    CHECK(mouseX>0 && mouseY<0);
    key(MkbMapper::R2,false); CHECK(state.right_x==mouseX && state.right_y==mouseY);
    key(MkbMapper::R2,true); CHECK(state.right_x==mouseX && state.right_y==mouseY+655);
    control(97); CHECK(state.right_x==mouseX && state.right_y==mouseY);
    control(97); CHECK(state.right_y==mouseY+655);
    control(100); CHECK(mapper.configuration().recoil_vertical==.02);
    CHECK(state.right_y==mouseY+819);
    control(98); CHECK(mapper.configuration().recoil_vertical==.02);
    key(MkbMapper::LookLeft,true); CHECK(state.right_x==-32767 && state.right_y==0);
    key(MkbMapper::LookLeft,false); CHECK(state.right_x==0 && state.right_y==655);
    CHECK(state.buttons==0); // Recoil control actions are local only.
    for(int i=0;i<100;++i) control(100);
    CHECK(mapper.configuration().recoil_vertical==.02 && state.right_y==8191);
    for(int i=0;i<100;++i) control(98);
    CHECK(mapper.configuration().recoil_vertical==.02 && state.right_y==0);
    mapper.release("escape"); CHECK(!mapper.active() && state.right_y==0 && !state.l2_state && !state.r2_state);
    CHECK(mapper.arm(true,true,false,2000)); g=mapper.generation();
    mapper.key(g,MkbMapper::L2,true,2001,2001);mapper.key(g,MkbMapper::R2,true,2001,2001);
    mapper.physical(g,100,true,2001,2001); // Strength increases; enable must still be off.
    CHECK(state.right_y==0 && mapper.configuration().recoil_vertical==.02);
    mapper.key(g,MkbMapper::RecoilToggle,true,2001,2001);CHECK(state.right_y==819); // Confirmed .02 plus the new .005 hotkey step.
    MkbMapper fresh([&](const ChiakiControllerState &s,const char *){state=s;},mapper.configuration());
    CHECK(fresh.arm(true,true,false,2100));const auto freshEpoch=fresh.generation();
    fresh.key(freshEpoch,MkbMapper::RecoilToggle,true,2101,2101);
    fresh.key(freshEpoch,MkbMapper::L2,true,2101,2101);fresh.key(freshEpoch,MkbMapper::R2,true,2101,2101);
    CHECK(state.right_y==655 && fresh.configuration().recoil_vertical==.02);
    auto enabledConfig=config;enabledConfig.recoil_enabled=true;
    MkbMapper configured([&](const ChiakiControllerState &s,const char *){state=s;},enabledConfig);
    CHECK(!configured.recoilState().enabled);
    CHECK(configured.arm(true,true,false,2200));auto configuredEpoch=configured.generation();
    CHECK(configured.recoilState().enabled && !configured.recoilState().applying);
    configured.report(configuredEpoch,0,0,3,2201,2201,2201);
    CHECK(configured.recoilState().aiming && configured.recoilState().firing && configured.recoilState().applying && state.right_y==655);
    configured.key(configuredEpoch,MkbMapper::LookDown,true,2202,2202);
    CHECK(!configured.recoilState().applying);
    configured.key(configuredEpoch,MkbMapper::LookDown,false,2203,2203);
    configured.key(configuredEpoch,MkbMapper::RecoilToggle,true,2204,2204);
    CHECK(!configured.recoilState().enabled && state.right_y==0 && configured.configuration().recoil_enabled);
    configured.release("configured-release");
    CHECK(!configured.recoilState().enabled && !configured.recoilState().aiming && !configured.recoilState().firing);
    CHECK(configured.arm(true,true,false,2300) && configured.recoilState().enabled);
    std::cout << "PASS recoil opt-in, trigger edges, local controls, repeat, menu, bounds and rearm\n";

    // Disabled recoil must not alter the prior mouse/controller output path.
    std::vector<ChiakiControllerState> baseline,disabled;
    auto zero=config;zero.recoil_vertical=0;
    MkbMapper a([&](const ChiakiControllerState &s,const char *){baseline.push_back(s);},zero);
    MkbMapper b([&](const ChiakiControllerState &s,const char *){disabled.push_back(s);},config);
    for(auto m:{&a,&b}) {
        CHECK(m->arm(true,true,false,3000)); const auto epoch=m->generation();
        m->motion(epoch,3,-4,3001,3001);m->tick(3008);
        m->report(epoch,-2,6,3,3009,3009,3009);m->tick(3016);
        m->key(epoch,MkbMapper::Cross,true,3017,3017);
        m->key(epoch,MkbMapper::LookUp,true,3018,3018);
        m->key(epoch,MkbMapper::LookUp,false,3019,3019);
        m->report(epoch,0,0,0,3020,3020,3020);m->tick(3024);m->release("test");
    }
    CHECK(baseline.size()==disabled.size());
    for(size_t i=0;i<baseline.size();++i) CHECK(chiaki_controller_state_equals(&baseline[i],&disabled[i]));
    std::cout << "PASS disabled recoil preserves complete controller output\n";

    // Whole HID reports combine button transitions and movement. Compare every
    // submission with the same input on a disabled mapper, including ADS flushes.
    for(bool differingModes:{false,true}) for(uint8_t releasedButtons:{uint8_t(0),uint8_t(1),uint8_t(2)}) {
        auto reportConfig=config;
        if(differingModes) reportConfig.ads.gain=.027;
        std::vector<ChiakiControllerState> activeStates,plainStates;
        MkbMapper active([&](const ChiakiControllerState &s,const char *){activeStates.push_back(s);},reportConfig);
        MkbMapper plain([&](const ChiakiControllerState &s,const char *){plainStates.push_back(s);},reportConfig);
        CHECK(active.arm(true,true,false,4000) && plain.arm(true,true,false,4000));
        active.key(active.generation(),MkbMapper::RecoilToggle,true,4001,4001);
        activeStates.clear();plainStates.clear();
        for(auto m:{&active,&plain}) {
            const auto epoch=m->generation();
            CHECK(m->report(epoch,2,-3,3,4002,4002,4002));m->tick(4008);
            m->motion(epoch,1,-2,4009,4009);
            CHECK(m->report(epoch,4,5,releasedButtons,4010,4010,4010));m->tick(4016);
            CHECK(m->report(epoch,0,0,0,4017,4017,4017));m->tick(4024);
        }
        CHECK(activeStates.size()==plainStates.size());
        for(size_t i=0;i<plainStates.size();++i) {
            auto expected=plainStates[i];
            if(expected.l2_state && expected.r2_state)
                expected.right_y=int16_t(std::min(32767,int(expected.right_y)+655));
            CHECK(chiaki_controller_state_equals(&activeStates[i],&expected));
        }
        CHECK(activeStates.back().right_y==0 && !activeStates.back().l2_state && !activeStates.back().r2_state);
    }
    std::cout << "PASS whole HID report recoil on/off, simultaneous releases, movement and differing ADS\n";

    for(double invalid:{-.001,.251,std::numeric_limits<double>::infinity(),std::numeric_limits<double>::quiet_NaN()}) {
        auto bad=config;bad.recoil_vertical=invalid;
        MkbMapper blocked([](const ChiakiControllerState &,const char *){CHECK(false);},bad);
        CHECK(!blocked.arm(true,true,false,1000));
    }
    for(double delta:{-100000.0,100000.0}) {
        MkbMapper m([&](const ChiakiControllerState &s,const char *){state=s;},config);
        CHECK(m.arm(true,true,false,1000));const auto epoch=m.generation();
        m.key(epoch,MkbMapper::RecoilToggle,true,1001,1001);
        m.key(epoch,MkbMapper::L2,true,1001,1001);m.key(epoch,MkbMapper::R2,true,1001,1001);
        m.motion(epoch,0,delta,1002,1002);m.tick(1008);
        CHECK(state.right_y==(delta>0?32767:-32767+655));
        CHECK(m.recoilState().applying==(delta<0));
    }
    auto subInteger=config;subInteger.recoil_vertical=.000001;subInteger.recoil_enabled=true;
    MkbMapper tiny([](const ChiakiControllerState &,const char *){},subInteger);
    CHECK(tiny.arm(true,true,false,1000));tiny.report(tiny.generation(),0,0,3,1001,1001,1001);
    CHECK(tiny.recoilState().enabled && !tiny.recoilState().applying);
    std::cout << "PASS recoil finite validation and saturation\n";
}

static void pulseRecoilChecks() {
    auto p=MkbProfile::defaults();p.recoil_enabled=true;p.recoil_pulse=true;
    p.recoil_vertical=.055;p.recoil_pulse_peak=.11;p.recoil_period_ms=120;p.radial_deadzone=.12;
    ChiakiControllerState out{};size_t submissions=0;
    MkbMapper m([&](const ChiakiControllerState &state,const char *){out=state;++submissions;},p);
    CHECK(m.arm(true,true,false,1000));auto g=m.generation();
    CHECK(m.report(g,0,0,3,1002,1002,1002));CHECK(out.right_y==3604);
    auto r=m.recoilState();CHECK(r.pulseMode && r.pulsePeak==.11 && r.pulsePeriodMs==120 && r.strength==.055 && r.applying && !r.pulsePause);
    m.tick(1008);CHECK(out.right_y==3604);
    m.tick(1057);CHECK(out.right_y==3604);
    m.tick(1058);CHECK(out.right_y==0 && !m.recoilState().applying && m.recoilState().pulsePause);
    m.tick(1097);CHECK(out.right_y==0);
    const auto before=submissions;m.tick(1122);CHECK(submissions==before+1 && out.right_y==3604);
    m.tick(1185);CHECK(out.right_y==3604);m.tick(1186);CHECK(out.right_y==0); // Next cycle spends 8 slots.
    CHECK(m.report(g,0,0,2,1187,1187,1187));CHECK(out.right_y==0 && !m.recoilState().pulsePause);
    CHECK(m.report(g,0,0,3,1188,1188,1188));CHECK(out.right_y==3604);
    m.key(g,MkbMapper::LookLeft,true,1189,1189);CHECK(out.right_x==-32767 && !m.recoilState().applying && !m.recoilState().pulsePause);
    m.key(g,MkbMapper::LookLeft,false,1190,1190);CHECK(out.right_y==3604);
    m.key(g,MkbMapper::RecoilToggle,true,1191,1191);CHECK(out.right_y==0 && !m.recoilState().pulsePause);
    m.key(g,MkbMapper::RecoilToggle,false,1192,1192);
    m.key(g,MkbMapper::RecoilToggle,true,1193,1193);CHECK(out.right_y==3604);
    m.release("pulse-release");CHECK(out.right_y==0 && !m.recoilState().applying && !m.recoilState().pulsePause);
    CHECK(m.arm(true,true,false,2000));g=m.generation();
    CHECK(m.report(g,0,0,3,2001,2001,2001));CHECK(out.right_y==3604);
    m.tick(2300);CHECK(!m.active() && out.right_y==0);
    CHECK(m.arm(true,true,false,3000));g=m.generation();
    CHECK(m.report(g,0,0,3,3001,3001,3001));m.tick(3008);m.tick(3007);
    CHECK(!m.active() && out.right_y==0);
    CHECK(m.configuration().radial_deadzone==.12 && m.configuration().recoil_vertical==.055);
    for(double invalid:{0.,.0009,.251,std::numeric_limits<double>::infinity(),std::numeric_limits<double>::quiet_NaN()}) {
        auto bad=p;bad.recoil_pulse_peak=invalid;
        MkbMapper blocked([](const ChiakiControllerState &,const char *){CHECK(false);},bad);
        CHECK(!blocked.arm(true,true,false,1000));
    }
    for(uint32_t invalid:{0u,31u,33u,36u,121u,500u,501u}) {
        auto bad=p;bad.recoil_period_ms=invalid;
        MkbMapper blocked([](const ChiakiControllerState &,const char *){CHECK(false);},bad);
        CHECK(!blocked.arm(true,true,false,1000));
    }
    CHECK(m.arm(true,true,false,4000));g=m.generation();
    CHECK(m.report(g,0,0,3,4001,4001,4100));CHECK(out.right_y==3604);
    m.tick(4155);CHECK(out.right_y==3604);m.tick(4156);CHECK(out.right_y==0);
    m.release("done");
    for(double target:{0.,.11,.12}) {
        auto q=p;q.recoil_vertical=target;
        MkbMapper duty([&](const ChiakiControllerState &state,const char *){out=state;},q);
        CHECK(duty.arm(true,true,false,5000));const auto epoch=duty.generation();
        duty.report(epoch,0,0,3,5001,5001,5001);
        CHECK(duty.recoilState().pulsePeak==std::max(target,.11));
        CHECK(duty.configuration().recoil_pulse_peak==.11);
        for(uint64_t t:{5008,5061,5062,5121,5248}) {
            duty.tick(t);CHECK(out.right_y==int(target*32767));CHECK(!duty.recoilState().pulsePause);
        }
    }
    // Two periods spend exactly 15 slots, avoiding the old minimum-pulse inflation.
    MkbMapper duty([&](const ChiakiControllerState &state,const char *){out=state;},p);
    CHECK(duty.arm(true,true,false,6000));g=duty.generation();duty.report(g,0,0,3,6001,6001,6001);
    int on=0;for(uint64_t t=6001;t<6241;t+=8){duty.tick(t);on+=out.right_y!=0;}
    CHECK(on==15);
    auto plain=p;plain.recoil_enabled=false;ChiakiControllerState reference{};
    MkbMapper active([&](const ChiakiControllerState &state,const char *){out=state;},p),disabled([&](const ChiakiControllerState &state,const char *){reference=state;},plain);
    for(auto mapper:{&active,&disabled}) {
        CHECK(mapper->arm(true,true,false,7000));const auto epoch=mapper->generation();
        mapper->report(epoch,0,0,3,7001,7001,7001);mapper->tick(7008);
        mapper->motion(epoch,1,-1,7009,7009);mapper->tick(7016);
    }
    auto expected=reference;expected.right_y=int16_t(std::min(32767,int(expected.right_y)+3604));
    CHECK(chiaki_controller_state_equals(&out,&expected));
    for(auto mapper:{&active,&disabled}){mapper->motion(mapper->generation(),1,-1,7050,7050);mapper->tick(7057);}
    CHECK(chiaki_controller_state_equals(&out,&reference));
    CHECK(active.recoilState().pulsePause && active.configuration().radial_deadzone==.12);
    // Saturation is not a waveform pause.
    active.motion(active.generation(),0,1e5,7058,7058);active.tick(7121);
    CHECK(out.right_y==32767 && !active.recoilState().applying && !active.recoilState().pulsePause);
    // Runtime adjustments reset phase, preserve config and cross zero/peak cleanly.
    auto tune=p;tune.recoil_vertical=.005;tune.recoil_pulse_peak=.01;tune.recoil_period_ms=32;
    MkbMapper adjusted([&](const ChiakiControllerState &state,const char *){out=state;},tune);
    CHECK(adjusted.arm(true,true,false,8000));g=adjusted.generation();adjusted.report(g,0,0,3,8001,8001,8001);
    adjusted.key(g,MkbMapper::RecoilDecrease,true,8002,8002);CHECK(out.right_y==0 && !adjusted.recoilState().pulsePause);
    adjusted.key(g,MkbMapper::RecoilDecrease,false,8003,8003);
    adjusted.key(g,MkbMapper::RecoilIncrease,true,8004,8004);CHECK(out.right_y==327);
    adjusted.key(g,MkbMapper::RecoilIncrease,false,8005,8005);
    adjusted.key(g,MkbMapper::RecoilIncrease,true,8006,8006);CHECK(out.right_y==327 && !adjusted.recoilState().pulsePause);
    adjusted.tick(8024);CHECK(out.right_y==327); // Full duty at peak.
    adjusted.key(g,MkbMapper::RecoilIncrease,false,8025,8025);adjusted.key(g,MkbMapper::RecoilIncrease,true,8026,8026);
    CHECK(out.right_y==491 && adjusted.recoilState().pulsePeak==.015);
    CHECK(adjusted.configuration().recoil_vertical==.005);

    // Weight actual 8-ms sample-and-hold output over many cycles and start offsets.
    const auto measured=[&](double target,double peak,uint32_t period,uint64_t offset,int samples) {
        auto q=p;q.recoil_vertical=target;q.recoil_pulse_peak=peak;q.recoil_period_ms=period;
        ChiakiControllerState sample{};MkbMapper sim([&](const ChiakiControllerState &state,const char *){sample=state;},q);
        CHECK(sim.arm(true,true,false,10000));const auto epoch=sim.generation();sim.report(epoch,0,0,3,10000,10000,10000);
        long long total=0;
        for(int i=0;i<samples;++i){sim.tick(10000+offset+uint64_t(i)*8);total+=sample.right_y;}
        return double(total)/(samples*32767.0);
    };
    for(uint64_t offset:{0,1,3,7}) {
        CHECK(std::abs(measured(.09,.11,120,offset,15000)-.09)<.0001);
        CHECK(std::abs(measured(.001,.25,32,offset,20000)-.001)<.00005);
        CHECK(std::abs(measured(.09,.11,128,offset,16000)-.09)<.0002);
        CHECK(std::abs(measured(.09,.11,496,offset,12400)-.09)<.0002);
        CHECK(std::abs(measured(.109,.11,40,offset,22000)-.109)<.0001);
    }
    std::cout<<"PASS pulse slot quota, actual 8ms weighting, offsets, tiny duty, jitter, controls and unchanged mouse\n";
}

static void rapidFireChecks() {
    auto profile=MkbProfile::defaults();profile.rapid_fire_enabled=true;
    profile.recoil_enabled=true;profile.recoil_vertical=.075;profile.recoil_pulse=true;
    profile.binds[17]=MkbMapper::RapidFireToggle;
    ChiakiControllerState state{};
    MkbMapper m([&](const ChiakiControllerState &s,const char *){state=s;},profile);
    CHECK(m.arm(true,true,false,1000));auto g=m.generation();
    m.key(g,MkbMapper::L2,true,1000,1000);m.key(g,MkbMapper::R2,true,1000,1000);
    CHECK(state.r2_state==255 && state.right_y>0);
    for(uint64_t t=1008;t<=1360;t+=8) {
        m.tick(t);CHECK(state.r2_state==(((t-1000)%120<40)?255:0));
        CHECK(m.recoilState().firing);
        if((t-1000)%120==40) CHECK(state.right_y>0); // recoil persists during R2 tap release
    }
    m.key(g,MkbMapper::R2,false,1361,1361);CHECK(state.r2_state==0 && state.right_y==0);
    m.key(g,MkbMapper::R2,true,1362,1362);CHECK(state.r2_state==255);
    m.tick(1402);CHECK(state.r2_state==0);
    m.physical(g,17,true,1403,1403);CHECK(state.r2_state==255); // disable while held
    m.physical(g,17,true,1404,1404,true);m.tick(1442);CHECK(state.r2_state==255);
    m.physical(g,17,false,1443,1443);m.physical(g,17,true,1444,1444);CHECK(state.r2_state==255);
    m.tick(1484);CHECK(state.r2_state==0);
    m.release("focus-lost");CHECK(!state.r2_state && !state.l2_state && !state.right_y);
    CHECK(m.arm(true,true,false,2000));g=m.generation();
    m.key(g,MkbMapper::R2,true,2000,2000);CHECK(state.r2_state==255 && state.right_y==0);
    m.tick(2040);CHECK(!state.r2_state);
    m.tick(2300);CHECK(!m.active() && !state.r2_state);
    profile.rapid_fire_interval_ms=64;profile.rapid_fire_press_ms=56;
    MkbMapper late([&](const ChiakiControllerState &s,const char *){state=s;},profile);
    CHECK(late.arm(true,true,false,2400));auto lg=late.generation();
    late.key(lg,MkbMapper::R2,true,2400,2400);late.tick(2448);CHECK(state.r2_state==255);
    late.tick(2464);CHECK(state.r2_state==0); // missed nominal release is still emitted
    late.key(lg,MkbMapper::Cross,true,2464,2464);CHECK(state.r2_state==0);
    late.tick(2471);CHECK(state.r2_state==0);late.tick(2472);CHECK(state.r2_state==255);
    late.tick(2600);CHECK(state.r2_state==0); // no overdue burst
    CHECK(late.recoilState().rapidFireEnabled);
    late.release("focus");CHECK(!late.recoilState().rapidFireEnabled);
    profile.rapid_fire_enabled=false;
    MkbMapper off([&](const ChiakiControllerState &s,const char *){state=s;},profile);
    CHECK(off.arm(true,true,false,3000));g=off.generation();
    off.key(g,MkbMapper::R2,true,3000,3000);
    for(uint64_t t=3008;t<3500;t+=8){off.tick(t);CHECK(state.r2_state==255);}
    off.key(g,MkbMapper::R2,false,3500,3500);CHECK(!state.r2_state);
    profile.rapid_fire_interval_ms=121;
    MkbMapper bad([](const ChiakiControllerState &,const char *){},profile);
    CHECK(!bad.arm(true,true,false,4000));
    std::cout << "PASS rapid fire timing, hold, release, toggle repeat, rearm, stall and recoil independence\n";
}

static void liveSettingsChecks() {
    auto p=MkbProfile::defaults();p.recoil_enabled=true;p.recoil_vertical=.02;
    p.binds[97]=MkbMapper::RecoilToggle;
    p.binds[98]=MkbMapper::RecoilIncrease;
    p.binds[99]=MkbMapper::RapidFireToggle;
    ChiakiControllerState a{},b{};
    MkbMapper mapper([&](const ChiakiControllerState &s,const char *){a=s;},p);
    MkbMapper reference([&](const ChiakiControllerState &s,const char *){b=s;},p);
    CHECK(mapper.arm(true,true,false,1000));CHECK(reference.arm(true,true,false,1000));
    const auto g=mapper.generation();
    for(auto m:{&mapper,&reference}) {m->physical(g,13,true,1001,1001);m->motion(g,5,-3,1002,1002);m->tick(1008);m->motion(g,2,4,1009,1009);}
    auto candidate=p;candidate.recoil_period_ms=160;
    CHECK(mapper.reconfigure(candidate,0));
    mapper.tick(1016);reference.tick(1016);
    CHECK(mapper.generation()==g && mapper.active());
    CHECK(a.left_y==b.left_y && a.right_x==b.right_x && a.right_y==b.right_y);
    mapper.physical(g,98,true,1017,1017);mapper.physical(g,98,false,1017,1017);
    CHECK(!mapper.reconfigure(candidate,0));
    candidate.recoil_vertical=.025;CHECK(mapper.reconfigure(candidate,mapper.runtimeRevision()));
    // Remapping an already held key releases its former action, then waits for up/down.
    candidate.binds[13]=MkbMapper::Cross;
    CHECK(mapper.reconfigure(candidate,mapper.runtimeRevision()));mapper.tick(1024);
    CHECK(a.left_y==0 && !(a.buttons&CHIAKI_CONTROLLER_BUTTON_CROSS));
    mapper.physical(g,13,true,1025,1025,true);CHECK(!(a.buttons&CHIAKI_CONTROLLER_BUTTON_CROSS));
    mapper.physical(g,13,false,1026,1026);mapper.physical(g,13,true,1027,1027);
    CHECK(a.buttons&CHIAKI_CONTROLLER_BUTTON_CROSS);
    candidate.binds.erase(13);CHECK(mapper.reconfigure(candidate,mapper.runtimeRevision()));
    CHECK(!(a.buttons&CHIAKI_CONTROLLER_BUTTON_CROSS));
    // Previously unmapped physical inputs are tracked too.
    mapper.physical(g,100,true,1028,1028);candidate.binds[100]=MkbMapper::Cross;
    CHECK(mapper.reconfigure(candidate,mapper.runtimeRevision()));
    mapper.physical(g,100,true,1029,1029,true);CHECK(!(a.buttons&CHIAKI_CONTROLLER_BUTTON_CROSS));
    mapper.physical(g,100,false,1030,1030);mapper.physical(g,100,true,1031,1031);
    CHECK(a.buttons&CHIAKI_CONTROLLER_BUTTON_CROSS);
    // Rebinding an already suppressed source must not release another alias.
    mapper.physical(g,101,true,1032,1032);candidate.binds[101]=MkbMapper::Cross;
    CHECK(mapper.reconfigure(candidate,mapper.runtimeRevision()));
    candidate.binds[101]=MkbMapper::Box;
    CHECK(mapper.reconfigure(candidate,mapper.runtimeRevision()));
    CHECK(a.buttons&CHIAKI_CONTROLLER_BUTTON_CROSS);
    // Events queued before the binding boundary cannot press the new action.
    candidate.binds[102]=MkbMapper::PS;
    CHECK(mapper.reconfigure(candidate,mapper.runtimeRevision(),1040));
    mapper.physical(g,102,true,1039,1041);CHECK(!(a.buttons&CHIAKI_CONTROLLER_BUTTON_PS));
    mapper.physical(g,102,false,1038,1041);mapper.physical(g,102,true,1039,1041);
    CHECK(!(a.buttons&CHIAKI_CONTROLLER_BUTTON_PS));
    mapper.physical(g,102,false,1042,1042);mapper.physical(g,102,true,1043,1043);
    CHECK(a.buttons&CHIAKI_CONTROLLER_BUTTON_PS);
    candidate.binds.erase(256);CHECK(mapper.reconfigure(candidate,mapper.runtimeRevision(),1045));
    candidate.binds[256]=MkbMapper::R2;
    CHECK(mapper.reconfigure(candidate,mapper.runtimeRevision(),1050));
    CHECK(mapper.report(g,0,0,1,1049,1049,1051));CHECK(!a.r2_state);
    CHECK(mapper.report(g,0,0,0,1048,1048,1051));
    CHECK(mapper.report(g,0,0,1,1049,1049,1051));CHECK(!a.r2_state);
    CHECK(mapper.report(g,0,0,0,1052,1052,1052));
    CHECK(mapper.report(g,0,0,1,1053,1053,1053));mapper.tick(1056);CHECK(a.r2_state);
    mapper.release("live-test");CHECK(!mapper.active());
    CHECK(mapper.reconfigure(p,0));CHECK(!mapper.active());
    std::cout<<"PASS live mapper preserves generation, holds and mouse; fences hotkeys and suppresses rebinds\n";
}

static void liveRearmAndRapidTiming() {
    auto p=MkbProfile::defaults();p.recoil_vertical=.02;p.recoil_enabled=true;p.rapid_fire_enabled=true;
    ChiakiControllerState state{};
    MkbMapper mapper([&](const ChiakiControllerState &s,const char *){state=s;},p);
    CHECK(mapper.arm(true,true,false,1000));auto g=mapper.generation();
    mapper.key(g,MkbMapper::RecoilIncrease,true,1001,1001);
    CHECK(mapper.recoilState().strength>.02);
    mapper.release("before-save");CHECK(mapper.arm(true,true,false,2000));g=mapper.generation();
    CHECK(mapper.recoilState().strength==.02);
    mapper.key(g,MkbMapper::R2,true,2000,2000);CHECK(state.r2_state==255);
    auto candidate=p;candidate.hip.gain=.02;
    CHECK(mapper.reconfigure(candidate,mapper.runtimeRevision(),2008));
    mapper.tick(2032);CHECK(state.r2_state==255);
    candidate.rapid_fire_press_ms=24;candidate.rapid_fire_interval_ms=80;
    CHECK(mapper.reconfigure(candidate,mapper.runtimeRevision(),2032));
    mapper.tick(2039);CHECK(state.r2_state==255);
    mapper.tick(2040);CHECK(state.r2_state==0); // Preserve original pressed deadline.
    candidate.name="unrelated";
    CHECK(mapper.reconfigure(candidate,mapper.runtimeRevision(),2041));
    candidate.rapid_fire_press_ms=32;candidate.rapid_fire_interval_ms=96;
    CHECK(mapper.reconfigure(candidate,mapper.runtimeRevision(),2048));
    mapper.tick(2095);CHECK(state.r2_state==0); // Preserve already scheduled 56 ms release.
    mapper.tick(2096);CHECK(state.r2_state==255);
    mapper.tick(2127);CHECK(state.r2_state==255);
    mapper.tick(2128);CHECK(state.r2_state==0); // Use the new 32 ms press.
    mapper.tick(2191);CHECK(state.r2_state==0);
    mapper.tick(2192);CHECK(state.r2_state==255); // Use the new 64 ms release.
    std::cout<<"PASS rearm restores confirmed strength; live timing preserves pending R2 edges\n";
}

static void rightHoldChecks() {
    auto p=MkbProfile::defaults();p.binds={{20,MkbMapper::HoldRight},{124,MkbMapper::Right},{123,MkbMapper::Left}};
    ChiakiControllerState out{};MkbMapper m([&](const ChiakiControllerState &s,const char *){out=s;},p);
    CHECK(m.arm(true,true,false,1000));auto g=m.generation();
    const auto right=CHIAKI_CONTROLLER_BUTTON_DPAD_RIGHT,left=CHIAKI_CONTROLLER_BUTTON_DPAD_LEFT;
    m.physical(g,20,true,1000,1000);m.physical(g,20,false,1001,1001);
    CHECK((out.buttons&right) && !(out.buttons&left));
    m.physical(g,124,true,1002,1002);
    for(uint64_t t=1008;t<=1600;t+=8)m.tick(t);
    CHECK(out.buttons&right);m.physical(g,124,false,1601,1601);CHECK(!(out.buttons&right));
    m.physical(g,20,true,1602,1602);CHECK(out.buttons&right);
    CHECK(m.reconfigure(p,0,1603));CHECK(!(out.buttons&right));
    m.tick(1664);CHECK(!(out.buttons&right));m.physical(g,20,false,1665,1665);
    m.physical(g,20,true,1666,1666);CHECK(out.buttons&right);
    m.release("focus-loss");CHECK(out.buttons==0);
    CHECK(m.arm(true,true,false,1700));m.tick(1708);CHECK(out.buttons==0);
    std::cout<<"PASS right hold after tap, no left output, manual alias, reconfigure and release cancellation\n";
}
static void gameplayChecks() {
    auto p=MkbProfile::defaults();p.binds={{20,MkbMapper::HoldLeft},{54,MkbMapper::Moon},
        {55,MkbMapper::Moon},{57,MkbMapper::DodgeRoll},{49,MkbMapper::Cross}};
    ChiakiControllerState out{};
    MkbMapper m([&](const ChiakiControllerState &s,const char *){out=s;},p);
    CHECK(m.arm(true,true,false,1000));auto g=m.generation();
    const auto left=CHIAKI_CONTROLLER_BUTTON_DPAD_LEFT, moon=CHIAKI_CONTROLLER_BUTTON_MOON;
    m.physical(g,20,true,1000,1000);m.physical(g,20,false,1001,1001);
    CHECK(out.buttons&left);
    for(uint64_t t=1008;t<1600;t+=8)m.tick(t);
    CHECK(out.buttons&left);m.tick(1600);CHECK(!(out.buttons&left));
    m.physical(g,57,true,1600,1600);CHECK(out.buttons&moon);
    m.physical(g,57,true,1601,1601,true); // No queued autorepeat.
    m.tick(1700);CHECK(!(out.buttons&moon)); // Late edge starts a full release.
    m.tick(1763);CHECK(!(out.buttons&moon));m.tick(1764);CHECK(out.buttons&moon);
    m.tick(1828);CHECK(!(out.buttons&moon));m.tick(1892);CHECK(!(out.buttons&moon));
    m.physical(g,54,true,1892,1892);m.physical(g,55,true,1893,1893);
    m.physical(g,54,false,1894,1894);CHECK(out.buttons&moon);
    m.physical(g,49,true,1894,1894);CHECK(out.buttons&CHIAKI_CONTROLLER_BUTTON_CROSS);
    m.physical(g,57,true,1895,1895);CHECK(!(out.buttons&moon));
    m.tick(1959);CHECK(out.buttons&moon);m.tick(2023);CHECK(!(out.buttons&moon));
    m.tick(2087);CHECK(out.buttons&moon);m.tick(2151);CHECK(!(out.buttons&moon));
    m.tick(2215);CHECK(out.buttons&moon); // Manual right/left Cmd count survives.
    m.physical(g,55,false,2215,2215);CHECK(!(out.buttons&moon));
    m.physical(g,57,true,2216,2216);CHECK(out.buttons&moon); // Next Caps toggle works.
    CHECK(m.reconfigure(p,0,2216));CHECK(!(out.buttons&moon));
    m.tick(2280);CHECK(!(out.buttons&moon));
    m.physical(g,20,true,2281,2281);CHECK(out.buttons&left);
    m.release("focus-loss");CHECK(out.buttons==0);CHECK(m.arm(true,true,false,2300));
    m.physical(g,57,true,2300,2300);CHECK(out.buttons==0); // Old generation rejected.
    m.tick(2364);CHECK(out.buttons==0);
    for(bool held:{false,true}) for(int phase=0;phase<5;++phase) {
        MkbMapper cancelled([&](const ChiakiControllerState &s,const char *){out=s;},p);
        CHECK(cancelled.arm(true,true,false,3000));const auto epoch=cancelled.generation();
        if(held)cancelled.physical(epoch,55,true,3000,3000);
        cancelled.physical(epoch,57,true,3001,3001);
        for(int i=1;i<=phase;++i)cancelled.tick(3001+64*i);
        const auto at=3001+64*phase;
        CHECK(cancelled.reconfigure(p,0,at));
        CHECK(bool(out.buttons&moon)==held);
        cancelled.physical(epoch,57,true,at,at); // Pre-apply queued pulse cannot restart.
        CHECK(bool(out.buttons&moon)==held);
        cancelled.tick(at+64);CHECK(bool(out.buttons&moon)==held);
        cancelled.release("focus-loss");CHECK(out.buttons==0);
        CHECK(cancelled.arm(true,true,false,at+65));cancelled.tick(at+129);CHECK(out.buttons==0);
    }
    MkbMapper manual([&](const ChiakiControllerState &s,const char *){out=s;},p);
    CHECK(manual.arm(true,true,false,4000));auto mg=manual.generation();
    manual.physical(mg,55,true,4000,4000);manual.physical(mg,57,true,4001,4001);
    manual.physical(mg,55,false,4002,4002); // Restore the current state, not a saved snapshot.
    for(uint64_t t=4065;t<=4321;t+=64)manual.tick(t);
    CHECK(!(out.buttons&moon));
    manual.physical(mg,20,true,4322,4322);
    for(uint64_t t=4330;t<4922;t+=8)manual.tick(t);
    manual.tick(4922);CHECK(!(out.buttons&left));
    manual.physical(mg,20,true,4923,4923,true);manual.tick(4930);CHECK(!(out.buttons&left));
    for(auto action:{MkbMapper::HoldLeft,MkbMapper::DodgeRoll}) for(bool mouse:{false,true}) {
        auto aliases=p;const int first=mouse?256:20,second=mouse?257:21;
        aliases.binds[first]=aliases.binds[second]=action;
        MkbMapper alias([&](const ChiakiControllerState &s,const char *){out=s;},aliases);
        CHECK(alias.arm(true,true,false,5000));const auto epoch=alias.generation();
        auto edge=[&](bool a,bool b,uint64_t at) {
            if(mouse) CHECK(alias.report(epoch,0,0,(a?1:0)|(b?2:0),at,at,at));
            else {alias.physical(epoch,first,a,at,at);alias.physical(epoch,second,b,at,at);}
        };
        const auto button=action==MkbMapper::HoldLeft?left:moon;
        edge(true,false,5000);CHECK(out.buttons&button);
        edge(true,true,5001); // Ignore a second alias while the sequence runs.
        const uint64_t end=action==MkbMapper::HoldLeft?5600:5256;
        for(uint64_t t=5008;t<end;t+=8)alias.tick(t);
        alias.tick(end);CHECK(!(out.buttons&button));
        edge(true,true,end+1);CHECK(!(out.buttons&button)); // Duplicate report cannot restart.
        edge(true,false,end+2);
        if(!mouse)alias.physical(epoch,second,true,end+3,end+3,true);
        CHECK(!(out.buttons&button));
        edge(true,true,end+4);CHECK(out.buttons&button); // First alias remains held.
        edge(false,true,end+5);CHECK(out.buttons&button); // Releasing it does not cancel.
    }
    std::cout << "PASS gameplay hold, late roll phases, Caps pulses, Cmd aliases, cancellation and overlapping keyboard/mouse aliases\n";
}

static void radialMenuChecks() {
    auto p=MkbProfile::defaults();p.radial_menu_enabled=true;p.radial_menu_open_delay_ms=0; // Проверка готового меню без задержки.
    p.radial_menu_gain=.1;
    p.radial_menu_actions={MkbMapper::L1,MkbMapper::R1,MkbMapper::Left};
    p.binds[12]=MkbMapper::L1;p.binds[3]=MkbMapper::R1;p.binds[5]=MkbMapper::Left;
    p.recoil_enabled=true;p.recoil_vertical=.1;
    std::vector<ChiakiControllerState> out;
    MkbMapper m([&](const ChiakiControllerState &s,const char *){out.push_back(s);},p);
    CHECK(m.arm(true,true,false,1000));auto g=m.generation();uint64_t t=1001;
    auto key=[&](int source,bool down){m.physical(g,source,down,t,t);++t;};
    auto move=[&](double x,double y){m.motion(g,x,y,t,t);m.tick(t++);};
    m.motion(g,2,0,t,t);++t;
    auto entry=out.size();key(12,true);
    CHECK(out.size()==entry+1 && !out[entry].right_x && (out[entry].buttons&CHIAKI_CONTROLLER_BUTTON_L1));
    CHECK(!out.back().right_x && !out.back().right_y);
    key(12,false);m.tick(t++);CHECK(!out.back().right_x && !out.back().right_y);
    for(int source:{12,3,5}) {
        key(source,true);CHECK(!out.back().right_x && !out.back().right_y);
        move(0,-5);CHECK(out.back().right_y==-16383);
        for(int i=0;i<10;++i)m.tick(t++);
        CHECK(out.back().right_y==-16383);
        key(49,true);CHECK(out.back().right_y==-16383);key(49,false);
        move(5,0);CHECK(out.back().right_x==16383 && out.back().right_y==-16383);
        move(100,0);move(100,0);move(-1,0);CHECK(out.back().right_x<30000 && out.back().right_x>29000);
        auto final=out.back();auto n=out.size();key(source,false);
        CHECK(out.size()==n+2 && out[n].right_x==final.right_x && out[n].buttons==final.buttons);
        CHECK(out.back().right_x==0 && out.back().right_y==0);
        m.tick(t++);CHECK(!out.back().right_x && !out.back().right_y);
    }
    auto extremeProfile=p;extremeProfile.radial_menu_gain=1;
    CHECK(m.reconfigure(extremeProfile,0,t++));
    key(12,true);move(std::numeric_limits<double>::max(),-std::numeric_limits<double>::max());
    CHECK(out.back().right_x==23169 && out.back().right_y==-23169);
    auto extreme=out.back();auto count=out.size();key(12,false);
    CHECK(out[count].right_x==extreme.right_x && out[count].right_y==extreme.right_y &&
        (out[count].buttons&CHIAKI_CONTROLLER_BUTTON_L1));
    CHECK(!out.back().right_x && !out.back().right_y && !(out.back().buttons&CHIAKI_CONTROLLER_BUTTON_L1));
    CHECK(m.reconfigure(p,0,t++));
    key(12,true);
    m.motion(g,3,-4,t,t);++t; // Close before the next timer tick.
    count=out.size();key(12,false);
    CHECK(out[count].right_x==9830 && out[count].right_y==-13106 &&
        (out[count].buttons&CHIAKI_CONTROLLER_BUTTON_L1));
    CHECK(!out.back().right_x && !out.back().right_y);
    key(12,true);move(5,0);
    key(3,true);CHECK(!out.back().right_x);move(0,5);
    key(12,false);CHECK(!out.back().right_y);key(3,false);
    key(12,true);move(5,0);
    m.key(g,MkbMapper::LookLeft,true,t,t);++t;CHECK(out.back().right_x==-32767);
    move(100,100);CHECK(out.back().right_x==-32767);
    m.key(g,MkbMapper::LookLeft,false,t,t);++t;CHECK(!out.back().right_x && !out.back().right_y);
    move(0,-5);
    auto changed=p;changed.name="Другое имя";changed.hip.gain=.03;
    CHECK(m.reconfigure(changed,0,t++));m.tick(t++);CHECK(out.back().right_y==-16383);
    changed.radial_menu_gain=.2;auto boundary=t++;
    CHECK(m.reconfigure(changed,0,boundary));CHECK(!out.back().right_y);
    m.motion(g,0,8,boundary-1,t);m.tick(t++);CHECK(!out.back().right_y);
    m.key(g,MkbMapper::L2,true,t,t);m.key(g,MkbMapper::R2,true,t,t);++t;
    CHECK(!out.back().right_y && !m.recoilState().applying);
    move(0,-2);CHECK(out.back().right_y==-13106);
    key(12,false);CHECK(!out.back().right_x && !out.back().right_y);
    m.tick(t++);CHECK(out.back().right_y==3276); // Normal recoil resumes after the neutral close edge.
    key(12,true);move(0,-2);CHECK(out.back().right_y==-13106);
    changed.binds[12]=MkbMapper::Box;boundary=t++;
    CHECK(m.reconfigure(changed,0,boundary));CHECK(!out.back().right_x);
    // Stale opener edge is fenced after a binding change.
    m.physical(g,12,true,boundary-1,t);CHECK(!(out.back().buttons&CHIAKI_CONTROLLER_BUTTON_BOX));
    for(const char *reason:{"focus-loss","release","stall"}) {
        m.release(reason);CHECK(!out.back().right_x && !out.back().right_y && !out.back().buttons);
        auto old=g;CHECK(m.arm(true,true,false,t));g=m.generation();++t;
        m.motion(old,100,100,t,t);m.tick(t++);CHECK(!out.back().right_x);
        key(3,true);move(5,0);CHECK(out.back().right_x>0);
    }
    m.tick(t+300);CHECK(!m.active() && !out.back().right_x);
    // Disabled and outside-menu configurations emit exactly the old sequence.
    for(bool enabled:{false,true}) {
        auto a=MkbProfile::defaults(), b=a;b.radial_menu_enabled=enabled;b.radial_menu_actions={MkbMapper::L1};
        std::vector<ChiakiControllerState> x,y;
        MkbMapper old([&](const ChiakiControllerState &s,const char *){x.push_back(s);},a), next([&](const ChiakiControllerState &s,const char *){y.push_back(s);},b);
        CHECK(old.arm(true,true,false,1000));CHECK(next.arm(true,true,false,1000));
        for(auto *mapper:{&old,&next}) {
            auto epoch=mapper->generation();mapper->motion(epoch,3,-8,1001,1001);mapper->tick(1008);
            mapper->key(epoch,MkbMapper::L2,true,1009,1009);mapper->key(epoch,MkbMapper::R2,true,1010,1010);
            mapper->motion(epoch,-2,4,1011,1011);mapper->tick(1016);mapper->tick(1024);mapper->release("end");
        }
        CHECK(x.size()==y.size());for(size_t i=0;i<x.size();++i)CHECK(chiaki_controller_state_equals(&x[i],&y[i]));
    }
    std::cout<<"PASS radial menus: Q/F/G latch, relative motion, disk clamp, final/release, overlap, Look priority, live edits, stale events, recoil, reset and camera equivalence\n";
}
static void radialBoundaryAndConfigChecks() {
    auto p=MkbProfile::defaults();p.radial_menu_enabled=true;p.radial_menu_open_delay_ms=0; // Проверка готового меню без задержки.
    p.radial_menu_actions={MkbMapper::L1};p.radial_menu_gain=.1;
    p.binds[12]=MkbMapper::L1;
    std::vector<ChiakiControllerState> out;
    MkbMapper m([&](const ChiakiControllerState &v,const char *){out.push_back(v);},p);
    CHECK(m.arm(true,true,false,1000));auto g=m.generation();
    CHECK(m.physicalIngest(g,12,true,1010,1020,1020));
    m.motion(g,9,0,1009,1021);m.tick(1024);CHECK(!out.back().right_x);
    m.motion(g,2,0,1011,1025);m.tick(1032);CHECK(out.back().right_x==6553);
    CHECK(m.physicalIngest(g,12,false,1033,1040,1040));CHECK(!out.back().right_x);
    m.motion(g,9,0,1032,1041);m.tick(1048);CHECK(!out.back().right_x);
    m.motion(g,2,0,1034,1049);m.tick(1056);CHECK(out.back().right_x>0);
    // A complete report owns its movement after its own transition.
    auto mouse=p;mouse.binds[256]=MkbMapper::L1;
    MkbMapper r([&](const ChiakiControllerState &v,const char *){out.push_back(v);},mouse);
    CHECK(r.arm(true,true,false,1000));g=r.generation();
    CHECK(r.report(g,2,0,1,1010,1020,1020));r.tick(1024);CHECK(out.back().right_x==6553);
    r.motion(g,9,0,1009,1025);r.tick(1032);CHECK(out.back().right_x==6553);
    CHECK(r.report(g,2,0,0,1033,1040,1040));CHECK(!out.back().right_x);
    r.tick(1048);CHECK(out.back().right_x>0);
    r.motion(g,9,0,1032,1049);r.tick(1056);CHECK(!out.back().right_x);
    CHECK(r.report(g,2,0,1,1033,1057,1057));r.tick(1064);CHECK(out.back().right_x==6553);


    // Edits outside a menu preserve both pending counts and cached camera state.
    for(bool enabled:{false,true}) {
        auto before=p;before.radial_menu_enabled=enabled;
        auto next=before;next.radial_menu_gain=.2;next.radial_menu_actions={MkbMapper::R1};
        std::vector<ChiakiControllerState> actual,expected;
        MkbMapper a([&](const ChiakiControllerState &v,const char *){actual.push_back(v);},before);
        MkbMapper b([&](const ChiakiControllerState &v,const char *){expected.push_back(v);},before);
        CHECK(a.arm(true,true,false,1000));CHECK(b.arm(true,true,false,1000));
        for(auto *v:{&a,&b}) {v->motion(v->generation(),3,0,1001,1001);v->tick(1008);v->motion(v->generation(),5,0,1009,1009);}
        auto n=actual.size();CHECK(a.reconfigure(next,0,1010));CHECK(actual.size()==n);
        a.tick(1016);b.tick(1016);CHECK(chiaki_controller_state_equals(&actual.back(),&expected.back()));
        a.motion(a.generation(),2,0,1009,1017);b.motion(b.generation(),2,0,1009,1017);
        a.tick(1024);b.tick(1024);CHECK(chiaki_controller_state_equals(&actual.back(),&expected.back()));
    }
    // Programmatic constructor/reconfigure reject the same radial configurations.
    std::vector<MkbProfile> invalid;
    auto bad=p;bad.radial_menu_open_delay_ms=1501;invalid.push_back(bad);
    bad=p;bad.radial_menu_gain=0;invalid.push_back(bad);
    bad=p;bad.radial_menu_gain=std::numeric_limits<double>::quiet_NaN();invalid.push_back(bad);
    bad=p;bad.radial_menu_actions={MkbMapper::HoldLeft};invalid.push_back(bad);
    bad=p;bad.radial_menu_actions={MkbMapper::L1,MkbMapper::L1};invalid.push_back(bad);
    bad=p;bad.radial_menu_actions.clear();invalid.push_back(bad);
    for(const auto &candidate:invalid) {
        MkbMapper rejected([](const ChiakiControllerState &,const char *){},candidate);
        CHECK(!rejected.arm(true,true,false,1000));
        MkbMapper live([&](const ChiakiControllerState &v,const char *){out.push_back(v);},p);
        CHECK(live.arm(true,true,false,1000));auto epoch=live.generation();
        live.key(epoch,MkbMapper::L1,true,1001,1001);live.motion(epoch,2,0,1002,1002);
        auto n=out.size();CHECK(!live.reconfigure(candidate,0,1003,true));live.flushReconfigure();
        CHECK(out.size()==n && live.generation()==epoch && live.runtimeRevision()==0 && live.configuration().radial_menu_gain==.1);
        live.tick(1008);CHECK(out.back().right_x==6553);
    }
    auto active=p;active.recoil_enabled=true;active.recoil_vertical=.1;active.rapid_fire_enabled=true;
    MkbMapper config([&](const ChiakiControllerState &v,const char *){out.push_back(v);},active);
    CHECK(config.arm(true,true,false,1000));g=config.generation();
    config.key(g,MkbMapper::L1,true,1001,1001);config.key(g,MkbMapper::L2,true,1001,1001);config.key(g,MkbMapper::R2,true,1001,1001);
    config.motion(g,2,0,1002,1002);config.tick(1048);CHECK(!out.back().r2_state && out.back().right_x==6553);
    auto next=active;next.radial_menu_gain=.2;CHECK(config.reconfigure(next,0,1049));CHECK(!out.back().right_x);
    next.radial_menu_enabled=false;next.recoil_enabled=false;next.rapid_fire_enabled=false;
    CHECK(config.reconfigure(next,0,1050));
    CHECK(!out.back().right_x && !out.back().right_y && out.back().l2_state==255 && out.back().r2_state==255);
    std::cout<<"PASS radial source-time fences, outside-camera edits, atomic config rejection and current runtime snapshot\n";
}

static void radialOpeningGuardChecks() {
    auto p=MkbProfile::defaults();p.radial_menu_enabled=true;p.radial_menu_gain=.1;
    p.radial_menu_actions={MkbMapper::L1,MkbMapper::R1,MkbMapper::Left};
    p.binds[12]=p.binds[13]=MkbMapper::L1;p.binds[14]=MkbMapper::R1;
    for(auto opener:{MkbMapper::L1,MkbMapper::R1,MkbMapper::Left}) {
        std::vector<ChiakiControllerState> out;
        MkbMapper m([&](const ChiakiControllerState &v,const char *){out.push_back(v);},p);
        CHECK(m.arm(true,true,false,1000));auto g=m.generation();
        m.motion(g,100,30,1001,1001);
        auto n=out.size();m.key(g,opener,true,1002,1002);
        CHECK(out.size()==n+1 && !out.back().right_x && !out.back().right_y);
        for(uint64_t t=1008;t<1352;t+=8) {
            m.motion(g,100,100,t,t);m.tick(t);
            CHECK(!out.back().right_x && !out.back().right_y);
        }
        m.key(g,MkbMapper::LookRight,true,1345,1345);CHECK(!out.back().right_x);
        m.key(g,MkbMapper::LookRight,false,1346,1346);
        m.tick(1352);CHECK(!out.back().right_x && !out.back().right_y);
        m.motion(g,100,100,1340,1353);m.tick(1360);
        CHECK(!out.back().right_x && !out.back().right_y);
        m.motion(g,2,-1,1361,1361);m.tick(1368);
        CHECK(out.back().right_x==6553 && out.back().right_y==-3276);
        m.tick(1376);CHECK(out.back().right_x==6553);
        auto next=p;next.hip.gain=.02;CHECK(m.reconfigure(next,0,1377));
        m.tick(1384);CHECK(out.back().right_x==6553);
        next.radial_menu_open_delay_ms=20;CHECK(m.reconfigure(next,0,1385));
        CHECK(!out.back().right_x);m.motion(g,5,0,1390,1390);m.tick(1400);CHECK(!out.back().right_x);
        m.tick(1405);CHECK(!out.back().right_x);
        m.motion(g,1,0,1406,1406);m.tick(1410);CHECK(out.back().right_x==3276);
        m.release("focus-loss");CHECK(!out.back().right_x);
        CHECK(m.arm(true,true,false,1500));g=m.generation();m.tick(1508);CHECK(!out.back().right_x);
        m.key(g,opener,true,1509,1509);m.tick(1760);CHECK(!m.active() && !out.back().right_x);
    }
    std::vector<ChiakiControllerState> out;
    MkbMapper m([&](const ChiakiControllerState &v,const char *){out.push_back(v);},p);
    CHECK(m.arm(true,true,false,1000));auto g=m.generation();
    m.physical(g,12,true,1001,1001);m.tick(1200);
    m.physical(g,13,true,1201,1201);
    auto next=p;next.hip.gain=.03;CHECK(m.reconfigure(next,0,1202));
    m.tick(1351);m.motion(g,1,0,1352,1352);m.tick(1353);CHECK(out.back().right_x==3276);
    m.physical(g,14,true,1354,1354);CHECK(!out.back().right_x);
    m.motion(g,10,0,1355,1355);m.tick(1360);CHECK(!out.back().right_x);
    m.physical(g,14,false,1361,1361);m.physical(g,12,false,1362,1362);
    m.physical(g,13,false,1363,1363);CHECK(!out.back().right_x);
    m.motion(g,2,0,1364,1364);m.tick(1370);CHECK(out.back().right_x>0);
    m.key(g,MkbMapper::L1,true,1371,1371);m.motion(g,90,0,1372,1372);
    m.key(g,MkbMapper::L1,false,1373,1373);m.tick(1380);CHECK(!out.back().right_x);
    m.motion(g,1,0,1381,1381);m.tick(1388);CHECK(out.back().right_x>0);
    std::cout<<"PASS radial opening guard: Q/F/G, pending camera, deadline, stale source, Look, aliases, config, tap, focus and stall\n";
}

static void radialReviewRegressionChecks() {
    auto p=MkbProfile::defaults();p.radial_menu_enabled=true;p.radial_menu_gain=.1;
    p.radial_menu_actions={MkbMapper::L1,MkbMapper::R1,MkbMapper::Left};
    p.binds[12]=MkbMapper::L1;p.binds[20]=MkbMapper::R1;
    // Rebinding an unused opener preserves both an ongoing guard and selection.
    {
        std::vector<ChiakiControllerState> out;
        MkbMapper m([&](const ChiakiControllerState &v,const char *){out.push_back(v);},p);
        CHECK(m.arm(true,true,false,1000));auto g=m.generation();
        m.physical(g,12,true,1001,1001);m.tick(1200);
        auto next=p;next.binds[20]=MkbMapper::Cross;
        auto count=out.size();CHECK(m.reconfigure(next,0,1201));CHECK(out.size()==count);
        m.tick(1350);CHECK(!out.back().right_x);
        m.motion(g,2,0,1351,1351);m.tick(1352);CHECK(out.back().right_x==6553);
        next.binds[20]=MkbMapper::Left;count=out.size();
        CHECK(m.reconfigure(next,0,1353));CHECK(out.size()==count);
        m.tick(1360);CHECK(out.back().right_x==6553);
    }
    // Look remains held across readiness, but cannot override the close snapshot.
    {
        std::vector<ChiakiControllerState> out;
        MkbMapper m([&](const ChiakiControllerState &v,const char *){out.push_back(v);},p);
        CHECK(m.arm(true,true,false,1000));auto g=m.generation();
        m.key(g,MkbMapper::LookRight,true,1001,1001);
        m.key(g,MkbMapper::L1,true,1002,1002);CHECK(!out.back().right_x);
        m.tick(1200);m.tick(1351);CHECK(!out.back().right_x);
        m.tick(1352);CHECK(out.back().right_x==32767);
        m.key(g,MkbMapper::L1,false,1353,1353);
        CHECK(!out.back().right_x && !out.back().right_y);
        m.key(g,MkbMapper::LookRight,false,1354,1354);
    }
    // The largest supported delay stays neutral throughout and cancels early.
    for(bool cancel:{false,true}) {
        auto slow=p;slow.radial_menu_open_delay_ms=1500;
        std::vector<ChiakiControllerState> out;
        MkbMapper m([&](const ChiakiControllerState &v,const char *){out.push_back(v);},slow);
        CHECK(m.arm(true,true,false,1000));auto g=m.generation();
        m.key(g,MkbMapper::L1,true,1001,1001);
        if(cancel) {
            m.motion(g,90,90,1050,1050);m.key(g,MkbMapper::L1,false,1100,1100);
            CHECK(!out.back().right_x && !out.back().right_y);
            m.motion(g,2,0,1101,1101);m.tick(1108);CHECK(out.back().right_x>0);
        } else {
            for(uint64_t t=1100;t<2501;t+=100) {
                m.motion(g,90,90,t,t);m.tick(t);
                CHECK(!out.back().right_x && !out.back().right_y);
            }
            m.tick(2501);CHECK(!out.back().right_x && !out.back().right_y);
            m.motion(g,2,0,2502,2502);m.tick(2508);CHECK(out.back().right_x==6553);
        }
    }
    // A deadline near the clock limit saturates instead of wrapping into the past.
    {
        const auto end=std::numeric_limits<uint64_t>::max();
        std::vector<ChiakiControllerState> out;
        MkbMapper m([&](const ChiakiControllerState &v,const char *){out.push_back(v);},p);
        CHECK(m.arm(true,true,false,end-100));auto g=m.generation();
        m.key(g,MkbMapper::L1,true,end-99,end-99);
        m.motion(g,2,0,end-50,end-50);m.tick(end-1);
        CHECK(!out.back().right_x && !out.back().right_y);
        m.key(g,MkbMapper::L1,false,end,end);CHECK(!out.back().right_x);
    }
    std::cout<<"PASS review regressions: unused opener rebind, held Look deadline/close, 1500ms/cancel and bounded deadline\n";
}
int main() {
    radialReviewRegressionChecks();
    radialOpeningGuardChecks();
    radialBoundaryAndConfigChecks();
    radialMenuChecks();
    rightHoldChecks();
    gameplayChecks();
    liveSettingsChecks();
    liveRearmAndRapidTiming();
    rapidFireChecks();
    recoilChecks();
    pulseRecoilChecks();
    {
        Fixture f;
        CHECK(!f.mapper.arm(false,true,false,1000));
        CHECK(!f.mapper.arm(true,false,false,1000));
        CHECK(!f.mapper.arm(true,true,true,1000));
        f.mapper.key(0,MkbMapper::W,true,1000,1000);
        f.mapper.motion(0,50,50,1000,1000); f.mapper.tick(1001);
        CHECK(f.sent.empty());
        std::cout << "PASS off / arm prerequisites\n";
    }
    {
        Fixture f; auto g=f.arm(); MkbEventQueue q;
        CHECK(q.push({g,1001,MkbMapper::Cross,true,0,0}));
        CHECK(q.push({g,1002,MkbMapper::Cross,false,0,0}));
        std::deque<MkbEventQueue::Event> events; std::string error;
        CHECK(q.drain(events,error)); CHECK(events.size()==2);
        for(auto &e:events) f.mapper.key(e.generation,static_cast<MkbMapper::Key>(e.key),e.down,e.timestamp,1003);
        CHECK(f.sent.size()==3); CHECK(f.sent[1].buttons==CHIAKI_CONTROLLER_BUTTON_CROSS); f.idle();
        std::cout << "PASS quick press/release preserved through production queue\n";
    }
    {
        Fixture f; auto g=f.arm();
        f.mapper.key(g,MkbMapper::W,true,1001,1001); f.mapper.key(g,MkbMapper::D,true,1002,1002);
        auto s=f.sent.back(); CHECK(s.left_x>0 && s.left_y<0);
        CHECK(std::hypot(double(s.left_x),double(s.left_y))<=32767);
        f.mapper.key(g,MkbMapper::A,true,1003,1003); CHECK(f.sent.back().left_x==0);
        f.mapper.key(g,MkbMapper::S,true,1004,1004); CHECK(f.sent.back().left_y==0);
        std::cout << "PASS diagonal / opposites\n";
    }
    {
        Fixture f; auto g=f.arm(); f.mapper.motion(g,100000,100000,1001,1001); f.mapper.tick(1008);
        auto s=f.sent.back(); CHECK(s.right_x>0 && s.right_y>0);
        CHECK(std::hypot(double(s.right_x),double(s.right_y))<=32767);
        f.mapper.tick(1016); f.idle();
        f.mapper.setSensitivity(0.001); f.mapper.motion(g,10,0,1017,1017); f.mapper.tick(1024);
        CHECK(f.sent.back().right_x==327);
        std::cout << "PASS mouse saturation / no tail / sensitivity\n";
    }
    {
        const uint32_t buttons[]={CHIAKI_CONTROLLER_BUTTON_CROSS,CHIAKI_CONTROLLER_BUTTON_BOX,
            CHIAKI_CONTROLLER_BUTTON_PYRAMID,CHIAKI_CONTROLLER_BUTTON_MOON,CHIAKI_CONTROLLER_BUTTON_L3,
            CHIAKI_CONTROLLER_BUTTON_L1,CHIAKI_CONTROLLER_BUTTON_R1,CHIAKI_CONTROLLER_BUTTON_DPAD_UP,
            CHIAKI_CONTROLLER_BUTTON_DPAD_DOWN,CHIAKI_CONTROLLER_BUTTON_DPAD_LEFT,CHIAKI_CONTROLLER_BUTTON_DPAD_RIGHT};
        Fixture f; auto g=f.arm();
        for(int k=MkbMapper::Cross;k<=MkbMapper::Right;++k) {
            f.mapper.key(g,static_cast<MkbMapper::Key>(k),true,1001,1001);
            CHECK(f.sent.back().buttons==buttons[k-MkbMapper::Cross]);
            f.mapper.key(g,static_cast<MkbMapper::Key>(k),false,1002,1002); f.idle();
        }
        f.mapper.key(g,MkbMapper::L2,true,1003,1003); CHECK(f.sent.back().l2_state==255);
        f.mapper.key(g,MkbMapper::R2,true,1004,1004); CHECK(f.sent.back().r2_state==255);
        std::cout << "PASS every button and trigger\n";
    }
    for(auto reason:{"escape","focus-loss","menu","stop","disconnect","sleep","device-unplug","event-tap-disabled","permission-denied"}) {
        Fixture f; auto g=f.arm(); MkbEventQueue q;
        f.mapper.key(g,MkbMapper::W,true,1001,1001); f.mapper.key(g,MkbMapper::L2,true,1002,1002);
        f.mapper.motion(g,40,20,1003,1003);
        CHECK(q.push({g,1003,MkbMapper::Cross,true,0,0})); q.fail(reason);
        std::deque<MkbEventQueue::Event> events; std::string error;
        CHECK(!q.drain(events,error)); CHECK(events.empty()); CHECK(error==reason);
        f.mapper.release(error.c_str()); CHECK(!f.mapper.active()); f.idle();
        auto next=f.arm(); CHECK(next!=g); size_t n=f.sent.size();
        f.mapper.key(g,MkbMapper::Cross,true,1004,1004); f.mapper.motion(g,50,50,1004,1004);
        CHECK(f.sent.size()==n);
        f.mapper.key(next,MkbMapper::Cross,true,999,1004);
        CHECK(f.sent.size()==n);
        f.mapper.tick(1008); f.idle();
        std::cout << "PASS release " << reason << " / stale generation / clean rearm\n";
    }
    {
        Fixture f; auto g=f.arm(); MkbEventQueue q;
        for(int i=0;i<512;++i) CHECK(q.push({g,1001,MkbMapper::Cross,bool(i%2),0,0}));
        CHECK(!q.push({g,1001,MkbMapper::Cross,true,0,0}));
        std::deque<MkbEventQueue::Event> events; std::string error;
        CHECK(!q.drain(events,error)); CHECK(events.empty()); CHECK(error=="queue-overflow");
        f.mapper.release(error.c_str()); f.idle();
        std::cout << "PASS overflow clears pending transitions / neutral\n";
    }
    {
        Fixture f; auto g=f.arm(); f.mapper.key(g,MkbMapper::W,true,1001,1102);
        CHECK(!f.mapper.active()); f.idle();
        g=f.arm(); f.mapper.motion(g,1,1,1002,1001); CHECK(!f.mapper.active()); f.idle();
        g=f.arm(); f.mapper.key(g,MkbMapper::W,true,1001,1001); f.mapper.tick(1251);
        CHECK(!f.mapper.active()); f.idle();
        std::cout << "PASS stale/future timestamps / handler stall\n";
    }
    {
        MkbSessionGate gate;
        auto notice=gate.start(); CHECK(gate.connected(notice));
        ChiakiControllerState held; chiaki_controller_state_set_idle(&held);
        held.buttons=CHIAKI_CONTROLLER_BUTTON_CROSS; held.left_x=32767;
        auto cache=gate.stateForSubmit(held,false); CHECK(cache.buttons!=0);
        gate.stop(); cache=gate.stateForSubmit(held,false);
        CHECK(cache.buttons==0 && cache.left_x==0 && cache.touches[0].id==-1);
        CHECK(!gate.connected(notice)); CHECK(!gate.ready());
        auto next=gate.start(); CHECK(!gate.connected(notice));
        cache=gate.stateForSubmit(held,false); CHECK(cache.buttons==0);
        CHECK(gate.connected(next));
        cache=gate.stateForSubmit(held,true); CHECK(cache.buttons==0);
        std::cout << "PASS quit/stop cached neutral / stale CONNECTED / retry gate\n";
    }

    {
        Fixture f; auto g=f.arm();
        f.mapper.motion(g,20,10,1001,1001); f.mapper.tick(1008);
        auto prior=f.sent.back(); CHECK(prior.right_x && prior.right_y);
        f.mapper.key(g,MkbMapper::Cross,true,1009,1009);
        CHECK(f.sent.back().right_x==prior.right_x && f.sent.back().right_y==prior.right_y);
        f.mapper.key(g,MkbMapper::Cross,false,1010,1010);
        CHECK(f.sent.back().right_x==prior.right_x && f.sent.back().right_y==prior.right_y);
        f.mapper.tick(1016); f.idle();
        f.mapper.motion(g,10,5,1017,1017); f.mapper.tick(1024);
        f.mapper.release("escape"); f.idle();
        std::cout << "PASS mouse/button interleave holds stick until tick; release neutral\n";
    }
    {
        MkbSessionGate gate; auto attempt=gate.start();
        auto quit=gate.quit(); CHECK(quit.generation==attempt && gate.canRetry(quit));
        gate.stop(); CHECK(!gate.canRetry(quit)); CHECK(!gate.isCurrent(quit.generation));
        CHECK(gate.shouldHandleQuit(quit));
        gate.start(); CHECK(!gate.shouldHandleQuit(quit));
        auto connectedNotice=gate.observeConnected();
        auto connectedQuit=gate.quit();
        CHECK(connectedQuit.wasConnected && !connectedQuit.retryEligible);
        CHECK(!gate.connected(connectedNotice)); CHECK(!gate.canRetry(connectedQuit));
        std::cout << "PASS QUIT then Stop before dispatch; CONNECTED+QUIT before dispatch\n";
    }
    {
        MkbSubmitLog log; ChiakiControllerState state; chiaki_controller_state_set_idle(&state);
        CHECK(log.record(state,"arm",false));
        for(int i=0;i<125;++i) CHECK(!log.record(state,"tick",false));
        state.right_x=100; CHECK(!log.record(state,"tick",false));
        CHECK(!log.record(state,"mode-segment",false));
        CHECK(log.record(state,"press",false));
        CHECK(log.record(state,"release",false));
        CHECK(!log.record(state,"tick",false));
        CHECK(log.record(state,"escape",false));
        CHECK(log.record(state,"tick",true));
        std::cout << "PASS mouse logging suppressed; buttons, resets and optional trace logged\n";
    }

}
