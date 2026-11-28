import assert from 'node:assert/strict';
import test from 'node:test';
import {mapCanonical,mapPayloadSchemas,validMapRequest as validateRequest} from './map-contract.mjs';
import {validMapRequest as validatePlanner} from '../workflow-planner/map-contract.mjs';

const oid='request.official.'+'a'.repeat(64), image='b'.repeat(64);
const regions=['start','end'].map(id=>({id,name:id,description:id,backgroundId:'background.'+image,imageSha256:image}));
const edge={id:'edge',from:'start',to:'end',label:'Next',hit:{x:20,y:30,width:120,height:80},spawn:{x:150,y:250}};
const graph={title:'Test',startRegionId:'start',regions,connections:[edge]};
const wall={id:'wall',regionId:'end',rect:{x:250,y:100,width:200,height:100}};
const update=graph=>({schemaVersion:'ngs.map-update.v1',projectId:'binari',graph,mapId:oid,expectedRevision:1});
const generate=graph=>({schemaVersion:'ngs.map-generate.v1',projectId:'binari',intent:'Keep the route',graph});
const validate=(payload,type,expected)=>{for(const fn of [validateRequest,validatePlanner])assert.equal(fn(payload,type),expected);};

test('typed map payload keeps colliders optional and byte-compatible for existing revisions',()=>{
 for(const i of [0,1])assert.equal(mapPayloadSchemas[i].properties.graph.required.includes('colliders'),false);
 assert.equal(mapPayloadSchemas[0].properties.graph.properties.colliders.maxItems,0);
 assert.equal(mapPayloadSchemas[1].properties.graph.properties.colliders.maxItems,96);
 const old=update(graph), before=mapCanonical(old);
 validate(old,'map.update',true);
 assert.equal(mapCanonical(old),before);
 assert.equal(Object.hasOwn(JSON.parse(before).graph,'colliders'),false);
 validate(update({...graph,colliders:[wall]}),'map.update',true);
 validate(update({...graph,colliders:[]}),'map.update',true);
});

test('map update rejects unknown regions, duplicate/invalid IDs, rectangles outside normalized bounds and oversize arrays',()=>{
 const bad=[
  null,[wall,wall],[{...wall,regionId:'missing'}],[{...wall,id:'Wall'}],[{...wall,label:'bad'}],
  [{...wall,rect:{...wall.rect,width:0}}],[{...wall,rect:{...wall.rect,x:900}}],
  [{...wall,rect:{...wall.rect,y:0.5}}],[{...wall,rect:{...wall.rect,x:true}}],
  Array.from({length:97},(_,i)=>({...wall,id:'w-'+i})),
 ];
 for(const colliders of bad)validate(update({...graph,colliders}),'map.update',false);
});

test('map generation remains layout-only and cannot add blocked areas',()=>{
 const initial={...graph,connections:[{...edge,hit:null,spawn:null}]};
 validate(generate(initial),'map.generate',true);
 validate(generate({...initial,colliders:[]}),'map.generate',true);
 validate(generate({...initial,colliders:[wall]}),'map.generate',false);
});

test('both request and planner validate new shapes and strict optional legacy geometry',()=>{
 const shape={...wall,shape:'ellipse',rotation:47};
 validate(update({...graph,colliders:[wall,{...shape,id:'round'}]}),'map.update',true);
 for(const bad of [{...wall,shape:'rect'},{...wall,rotation:0},{...shape,rotation:360},{...shape,shape:'triangle'}])
  validate(update({...graph,colliders:[bad]}),'map.update',false);
 const options=mapPayloadSchemas[1].properties.graph.properties.colliders.items.oneOf;
 assert.deepEqual(options[1].properties.shape.enum,['rect','ellipse']);
 assert.equal(options[1].properties.rotation.maximum,359);
});

test('map placement image scale is optional and independent of placement scale',()=>{
 const decoration={id:'lantern',regionId:'start',prop:{propId:'lantern',revision:2,sha256:image},anchor:{x:20,y:30},scale:125,visibility:'both'};
 validate(update({...graph,decorations:[decoration]}),'map.update',true);
 for(const imageScalePercent of [5,10,20,30,40,50,60,70,80,90,100])
  validate(update({...graph,decorations:[{...decoration,imageScalePercent}]}),'map.update',true);
 for(const imageScalePercent of [0,15,95,110,50.5,'50',true,null])
  validate(update({...graph,decorations:[{...decoration,imageScalePercent}]}),'map.update',false);
});
