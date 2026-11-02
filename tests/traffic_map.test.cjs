const test = require('node:test');
const assert = require('node:assert/strict');
const {bodyCenter, vehicleDisplayMode, angleDelta, offsetPolyline, motionPlan, sampleMotion, movementSignals} = require('../signal_demo/traffic_map.js');
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-8, `${a} != ${b}`);

test('front bumper coordinate: body extends behind north/east/south/west heading', () => {
  const expected = [[100,97.5],[97.5,100],[100,102.5],[102.5,100]];
  for (const [i, angle] of [0,90,180,270].entries()) {
    const p = bodyCenter({x:100,y:100,angle,length:5});
    p.forEach((v,k) => near(v, expected[i][k]));
  }
});
test('lane boundaries are offset half a physical lane width, not drawn on its centre', () => {
  assert.deepEqual(offsetPolyline([[0,0],[10,0]], 1.6), [[0,1.6],[10,1.6]]);
  assert.deepEqual(offsetPolyline([[0,0],[0,10]], -1), [[1,0],[1,10]]);
  assert.deepEqual(offsetPolyline([[0,0]], 1), [[0,0]]);
});
test('rotation crosses 359/0 by the short path', () => {
  assert.equal(angleDelta(359, 1), 2);
  assert.equal(angleDelta(1, 359), -2);
});
test('overview markers are separate from physical dimensions; detail always uses metres', () => {
  assert.equal(vehicleDisplayMode({length:4.5},.1),'minimum-marker');
  assert.equal(vehicleDisplayMode({length:12},1),'physical-dimensions');
  assert.equal(vehicleDisplayMode({length:2},1.3),'physical-dimensions');
});
const lane = {width:3.2,shape:[[0,0],[10,0],[10,10]]};
const first = {id:'v',x:8,y:0,angle:90,speed:4,lane_id:':j_0'};
const last = {...first,x:10,y:2,angle:0};
const context = {sameRun:true,time:2,previousTime:1,status:'running',previousStatus:'running',speed:1,wallGap:300};
test('interpolation follows the observed curved lane, never the diagonal through its corner', () => {
  const plan = motionPlan(first,last,context,lane);
  assert.equal(plan.animate,true);
  const middle = sampleMotion(plan,.5);
  near(middle.x,10);near(middle.y,0);
  assert.deepEqual(sampleMotion(plan,0),first);
  assert.deepEqual(sampleMotion(plan,1),last);
});
test('pause, resume, new run, 30x/60x, gaps and lane changes snap to authoritative state', () => {
  for (const delta of [{status:'paused'},{previousStatus:'paused'},{sameRun:false},{speed:30},{speed:60},
    {time:6},{wallGap:2000}]) {
    const plan=motionPlan(first,last,{...context,...delta},lane);
    assert.equal(plan.animate,false);
    assert.deepEqual(sampleMotion(plan,.25),last);
  }
  assert.equal(motionPlan(first,{...last,lane_id:'next'},context,lane).animate,false);
  assert.equal(motionPlan(first,{...last,x:300},context,lane).animate,false);
  assert.equal(motionPlan(first,{...last,x:5,y:5},context,lane).animate,false);
});
test('1x/5x/10x allow bounded observed interpolation only', () => {
  for (const speed of [1,5,10]) assert.equal(motionPlan(first,last,{...context,speed},lane).animate,true);
});
test('a shared lane green straight movement never turns its red left movement green', () => {
  const data={signals:{j:{movements:[{index:0,incoming:'a',outgoing:'b',turn:'s'},
    {index:1,incoming:'a',outgoing:'c',turn:'l'}]}}};
  assert.deepEqual(movementSignals(data,[{id:'j',state:'Gr'}]).map(m=>m.color),['G','r']);
  assert.deepEqual(movementSignals(data,[{id:'j',state:'yy'}]).map(m=>m.color),['y','y']);
  assert.deepEqual(movementSignals(data,[{id:'j',state:'rr'}]).map(m=>m.color),['r','r']);
});
