/** Compare the live TS engine against a freshly generated Python oracle. */
import { readFileSync, writeFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import { resolve } from 'node:path'

const [root, oraclePath, outputPath] = process.argv.slice(2)
if (!root || !oraclePath || !outputPath) throw new Error('Usage: tsx audit_toporide_physics.mts TOPORIDE_ROOT ORACLE_JSON REPORT_JSON')
const { MotionEngine } = await import(pathToFileURL(resolve(root, 'src/engine/physics.ts')).href)
const { search } = await import(pathToFileURL(resolve(root, 'src/engine/search.ts')).href)
const {trainNetwork,influence}=await import(pathToFileURL(resolve(root,'src/engine/network.ts')).href)
const oracle = JSON.parse(readFileSync(oraclePath, 'utf8'))
const mobility = { riderMass:75, bikeMass:12, crr:.007, cda:.55, efficiency:.97, accelerationLimit:2 }
const fields = {
  time:'time_s', finalSpeed:'final_speed_mps', humanWork:'rider_energy_j', wheelWork:'mechanical_energy_j',
  aero:'aero_loss_j', rolling:'rolling_loss_j', braking:'braking_energy_j',
  kineticAssist:'kinetic_assist_j', climbEntryKinetic:'climb_entry_kinetic_j',
}
const failures: any[] = []
let worstRelative = 0
for (const c of oracle.cases) {
  const engine = new MotionEngine(mobility, {density:1.225, gravity:9.80665, speedCap:c.cap, headwind:c.wind,windSpeed:c.windSpeed,windFrom:c.windFrom},150,5,5,c.constant)
  const actual = engine.traverse({id:'road',from:1,to:2,meters:100,points:[{lat:0,lon:0},{lat:.001,lon:0}],samples:[{distance:0,elevation:0},{distance:100,elevation:c.rise}]},c.speed)
  const errors: any = {}
  for (const [ts, py] of Object.entries(fields)) {
    const expected = c.expected[py], delta = Math.abs(actual[ts]-expected)
    const relative = delta / Math.max(1,Math.abs(expected))
    worstRelative = Math.max(worstRelative,relative)
    if (!Number.isFinite(actual[ts]) || delta > 1e-8 + 1e-10*Math.abs(expected)) errors[ts]={actual:actual[ts],expected,delta}
  }
  if (Object.keys(errors).length) failures.push({...c,expected:undefined,errors})
}
const report = {total:oracle.cases.length,passed:oracle.cases.length-failures.length,failed:failures.length,worstRelative,failures}
const routeFailures: any[] = []
for (const c of oracle.routeCases ?? []) {
  const engine = new MotionEngine(mobility,{density:1.225,gravity:9.80665,speedCap:null,headwind:c.wind},150)
  const graph = {nodes:new Map(Array.from({length:5},(_,i)=>[i,{lat:0,lon:i*.001}])),arcs:new Map(c.arcs.map((a:any)=>[a.id,{...a,from:a.fromNode,points:[{lat:0,lon:a.fromNode*.001},{lat:0,lon:a.to*.001}]}])),turns:[],warnings:[]}
  const result = search(graph,engine,0,4,c.profile,{exactSpeed:c.exact,maxExpansions:null,maxLabelsPerState:null})
  const actual = result.labels.map((last:any)=>{
    const arcs:string[]=[]
    for(let l=last;l.parent;l=l.parent) arcs.push(l.part.arcId)
    return {arcs:arcs.reverse(),time:last.time,distance:last.distance,ascent:last.ascent,energy:last.energy}
  })
  const key = (r:any)=>JSON.stringify(r.arcs)
  if (actual.length!==c.routes.length || actual.some((a:any)=>{
    const expected=c.routes.find((r:any)=>key(r)===key(a))
    return !expected || ['time','distance','ascent','energy'].some(k=>Math.abs(a[k]-expected[k])>1e-7)
  })) routeFailures.push({profile:c.profile,wind:c.wind,exact:c.exact,actual,expected:c.routes})
}
Object.assign(report,{routeTotal:oracle.routeCases?.length??0,routeFailures})
const networkFailures:any[]=[]
for(const c of oracle.network?.models??[]) {
  const actual=trainNetwork(c.profile,oracle.network.parts)
  for(const [ts,py] of Object.entries({means:'means',scales:'scales',targetMeans:'target_means',targetScales:'target_scales',w1:'hidden_weights',w2:'output_weights',b1:'hidden_bias',b2:'output_bias',validationMse:'validation_mse'})) {
    const a=[actual[ts]].flat(Infinity),b=[c.model[py]].flat(Infinity)
    const error=Math.max(...a.map((v:number,i:number)=>Math.abs(v-b[i])))
    if(error>1e-9 || !Number.isFinite(error))networkFailures.push({profile:c.profile,field:ts,error})
  }
  for(const [name,value] of Object.entries(influence(actual)))
    if(Math.abs(Number(value)-c.influence[name])>1e-9)networkFailures.push({profile:c.profile,field:name})
}
Object.assign(report,{networkTotal:oracle.network?.models.length??0,networkFailures})
writeFileSync(outputPath,JSON.stringify(report,null,2))
console.log(JSON.stringify({...report,failures:failures.slice(0,3)},null,2))
process.exitCode = failures.length || routeFailures.length || networkFailures.length ? 1 : 0
