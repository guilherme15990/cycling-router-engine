/** Export one hydrated/snapped saved graph, then compare both search engines. */
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
const [root, input, output] = process.argv.slice(2)
const load = (p:string)=>import(pathToFileURL(resolve(root,p)).href)
const { buildGraph }=await load('src/infrastructure/overpass.ts')
const { hydrateGraph }=await load('src/infrastructure/hydrate.ts')
const { parseAAIGrid,gridSample }=await load('src/infrastructure/elevation.ts')
const { adaptGraph,snapQueries }=await load('src/engine/graph.ts')
const { MotionEngine }=await load('src/engine/physics.ts')
const { search }=await load('src/engine/search.ts')
const data=JSON.parse(readFileSync(input,'utf8'))
const grid=parseAAIGrid(data.dem.raw)
if(!grid) throw new Error('Invalid saved DEM')
const source=buildGraph(data.current.ways,{contraflow:false})
hydrateGraph(source,{mode:'grid',provider:data.dem.provider,sample:(lat:number,lon:number)=>gridSample(grid,lat,lon)??NaN})
const {graph,nodes}=snapQueries(adaptGraph(source),data.points)
const mobility={riderMass:75,bikeMass:12,crr:.007,cda:.55,efficiency:.97,accelerationLimit:2}
const environment={density:1.225,gravity:9.80665,speedCap:null,headwind:0}
const result=search(graph,new MotionEngine(mobility,environment,150),nodes[0],nodes[1],'flattest',{maxExpansions:60000,maxLabelsPerState:null})
const routes=result.labels.map((last:any)=>{
  const arcs=[];for(let l=last;l.parent;l=l.parent)arcs.push(l.part.arcId)
  return {arcs:arcs.reverse(),time:last.time,distance:last.distance,ascent:last.ascent,energy:last.energy}
})
const payload={nodes:[...graph.nodes.entries()],arcs:[...graph.arcs.values()],start:nodes[0],end:nodes[1],profile:'flattest',mobility,environment,ts:{...result,labels:undefined,routes}}
writeFileSync(output,JSON.stringify(payload))
console.log(JSON.stringify({nodes:graph.nodes.size,arcs:graph.arcs.size,expanded:result.expanded,truncated:result.truncated,routes:routes.length,best:routes.sort((a:any,b:any)=>a.ascent-b.ascent||a.distance-b.distance)[0]}))
