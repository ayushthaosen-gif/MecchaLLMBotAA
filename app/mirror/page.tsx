"use client";
import {useEffect,useRef,useState} from "react";
import {DrawingUtils,FilesetResolver,PoseLandmarker} from "@mediapipe/tasks-vision";

type Joints={right_shoulder:number;right_elbow:number;left_shoulder:number;left_elbow:number};
type Pos={x:number;y:number};
type JointPositions={right_shoulder:Pos;right_elbow:Pos;left_shoulder:Pos;left_elbow:Pos};
const clamp=(v:number,a=15,b=165)=>Math.max(a,Math.min(b,v));
function angle(a:{x:number;y:number},b:{x:number;y:number},c:{x:number;y:number}){
 const u={x:a.x-b.x,y:a.y-b.y},v={x:c.x-b.x,y:c.y-b.y};
 const d=Math.hypot(u.x,u.y)*Math.hypot(v.x,v.y);return d?Math.acos(clamp((u.x*v.x+u.y*v.y)/d,-1,1))*180/Math.PI:90;
}
function side(l:any[],shoulder:number,elbow:number,wrist:number,hip:number){
 const elevation=angle(l[hip],l[shoulder],l[elbow]);
 return {shoulder:clamp(30+elevation*2/3),elbow:clamp(angle(l[shoulder],l[elbow],l[wrist]))};
}

// Small glowing dot + crosshair at a tracked joint — drawn on the same
// (mirrored) canvas as the wireframe, so it stays pixel-locked to the
// joint. Angle text is NOT drawn here — canvas text would render
// mirror-flipped/backwards along with the video (see .camera's
// transform:scaleX(-1)); the numeric labels are separate, non-mirrored
// DOM chips positioned over the video instead (see JointChip below).
function drawJointMarker(ctx:CanvasRenderingContext2D,x:number,y:number,active:boolean){
 ctx.save();
 ctx.shadowColor=active?"#4fd8e0":"#ffb02080";ctx.shadowBlur=active?14:6;
 ctx.strokeStyle=active?"#4fd8e0":"#ffb020a0";ctx.lineWidth=1.5;
 ctx.beginPath();ctx.arc(x,y,9,0,Math.PI*2);ctx.stroke();
 ctx.beginPath();ctx.moveTo(x-14,y);ctx.lineTo(x-5,y);ctx.moveTo(x+5,y);ctx.lineTo(x+14,y);
 ctx.moveTo(x,y-14);ctx.lineTo(x,y-5);ctx.moveTo(x,y+5);ctx.lineTo(x,y+14);ctx.stroke();
 ctx.restore();
}

function JointChip({label,pos,angleDeg,visible}:{label:string;pos:Pos;angleDeg:number;visible:boolean}){
 // pos.x/.y are normalized [0,1] in the RAW (unmirrored) camera frame.
 // This chip is a sibling of the mirrored video/canvas, not a mirrored
 // element itself, so its own position must be flipped by hand
 // (1 - x) to land on the correct spot in the mirrored display.
 return <div className={`jointChip${visible?" live":""}`} style={{left:`${(1-pos.x)*100}%`,top:`${pos.y*100}%`}}>
  <span className="jointChipLabel">{label}</span><span className="jointChipAngle">{Math.round(angleDeg)}°</span>
 </div>;
}

export default function MirrorPage(){
 const video=useRef<HTMLVideoElement>(null),canvas=useRef<HTMLCanvasElement>(null),tracker=useRef<PoseLandmarker|null>(null),raf=useRef(0),lastSent=useRef(0),sending=useRef(false),smooth=useRef<Joints|null>(null);
 const fpsRef=useRef(0),lastFrameRef=useRef(performance.now()),lastHudRef=useRef(0);
 const [running,setRunning]=useState(false),[status,setStatus]=useState("Camera stopped"),[endpoint,setEndpoint]=useState(""),[token,setToken]=useState("");
 const [hud,setHud]=useState({fps:0,latencyMs:0,visible:false,streaming:false});
 const [joints,setJoints]=useState<Joints|null>(null);
 const [positions,setPositions]=useState<JointPositions|null>(null);
 useEffect(()=>{const chat=localStorage.getItem("meccanoid.endpoint")||"";setEndpoint(localStorage.getItem("meccanoid.mirrorEndpoint")||chat.replace(/\/chat\/?$/,"/mirror_pose"));setToken(localStorage.getItem("meccanoid.token")||"");return()=>{stop(false);tracker.current?.close()}},[]);
 function stop(report=true){cancelAnimationFrame(raf.current);video.current?.srcObject instanceof MediaStream&&video.current.srcObject.getTracks().forEach(t=>t.stop());setRunning(false);setHud(h=>({...h,visible:false,streaming:false}));if(report)setStatus("Camera stopped — robot auto-rests within 750 ms.")}
 async function start(){
  try{setStatus("Loading on-device pose model…");const vision=await FilesetResolver.forVisionTasks("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.22-rc.20250304/wasm");
   tracker.current??=await PoseLandmarker.createFromOptions(vision,{baseOptions:{modelAssetPath:"https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",delegate:"GPU"},runningMode:"VIDEO",numPoses:1,minPoseDetectionConfidence:.6,minTrackingConfidence:.6});
   const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:"user",width:{ideal:960},height:{ideal:720}},audio:false});if(!video.current)return;video.current.srcObject=stream;await video.current.play();setRunning(true);setStatus("Tracking locally");lastFrameRef.current=performance.now();loop();
  }catch(e){stop(false);setStatus(e instanceof Error?e.message:"Unable to start camera")}
 }
 function loop(){
  const v=video.current,c=canvas.current,t=tracker.current;if(!v||!c||!t||v.readyState<2){raf.current=requestAnimationFrame(loop);return}
  c.width=v.videoWidth;c.height=v.videoHeight;const ctx=c.getContext("2d")!;ctx.clearRect(0,0,c.width,c.height);

  const now=performance.now();
  const dt=now-lastFrameRef.current;lastFrameRef.current=now;
  if(dt>0)fpsRef.current=fpsRef.current?fpsRef.current*.9+(1000/dt)*.1:1000/dt;

  const detectStart=performance.now();
  const result=t.detectForVideo(v,now);
  const latencyMs=performance.now()-detectStart;

  const trackedOk=result.landmarks[0]&&[11,12,13,14,15,16,23,24].every(i=>(result.landmarks[0][i].visibility??1)>.45);
  let liveJoints:Joints|null=null,livePositions:JointPositions|null=null;

  if(trackedOk){
   const l=result.landmarks[0];
   new DrawingUtils(ctx).drawConnectors(l,PoseLandmarker.POSE_CONNECTIONS,{color:"#4fd8e055",lineWidth:2});
   // Front-camera mirror: the person's left arm drives the robot's right arm.
   const right=side(l,11,13,15,23),left=side(l,12,14,16,24);
   const raw:Joints={right_shoulder:right.shoulder,right_elbow:right.elbow,left_shoulder:left.shoulder,left_elbow:left.elbow};
   const prev=smooth.current||raw;const smoothed=Object.fromEntries(Object.entries(raw).map(([k,x])=>[k,Math.round((prev as any)[k]*.7+x*.3)])) as Joints;
   smooth.current=smoothed;liveJoints=smoothed;
   livePositions={right_shoulder:{x:l[11].x,y:l[11].y},right_elbow:{x:l[13].x,y:l[13].y},left_shoulder:{x:l[12].x,y:l[12].y},left_elbow:{x:l[14].x,y:l[14].y}};
   for(const p of Object.values(livePositions))drawJointMarker(ctx,p.x*c.width,p.y*c.height,true);

   if(endpoint&&now-lastSent.current>100&&!sending.current){lastSent.current=now;sending.current=true;fetch(endpoint,{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:`Bearer ${token}`}:{})},body:JSON.stringify({joints:smoothed})}).then(r=>{if(!r.ok)throw new Error(`Mirror endpoint HTTP ${r.status}`)}).catch(e=>setStatus(e.message)).finally(()=>{sending.current=false})}
  }else setStatus("Move back until shoulders, elbows and wrists are visible");

  // "Streaming" reads straight off lastSent.current (a ref, always
  // current) rather than the hud state — loop() recurses via its own
  // requestAnimationFrame call, not through React re-renders, so a
  // captured `hud` value here would just be whatever it was on the
  // very first call and never update (a stale-closure trap).
  const streaming=!!endpoint&&trackedOk&&(now-lastSent.current<300);

  // React state (and therefore the DOM/HUD) is throttled to ~8/s —
  // plenty smooth for numbers changing under smoothed tracking, and
  // far cheaper than re-rendering on every one of the ~60 canvas
  // frames/sec the wireframe itself draws at.
  if(now-lastHudRef.current>120){
   lastHudRef.current=now;
   setHud({fps:Math.round(fpsRef.current),latencyMs:Math.round(latencyMs),visible:trackedOk,streaming});
   if(liveJoints)setJoints(liveJoints);
   if(livePositions)setPositions(livePositions);
  }
  raf.current=requestAnimationFrame(loop);
 }
 function save(){localStorage.setItem("meccanoid.mirrorEndpoint",endpoint.trim());setStatus(endpoint?"Mirror endpoint saved":"Wireframe-only mode")}
 const chips:[string,keyof Joints][]=[["R-SHOULDER","right_shoulder"],["R-ELBOW","right_elbow"],["L-SHOULDER","left_shoulder"],["L-ELBOW","left_elbow"]];
 return <main className="mirrorPage"><header><a href="/">← Console</a><div className="brand"><b>MECCANOID</b> // POSE MIRROR</div></header>
 <section className="panel"><h1>Copy my arm movements</h1><p className="note">Your camera is processed on this device. Only four joint angles are transmitted; video never goes to the robot or cloud.</p>
  <div className="camera">
   <video ref={video} playsInline muted/><canvas ref={canvas}/>
   <div className="hudCorners"><i/><i/><i/><i/></div>
   <div className={`hudReadout${hud.visible?" live":""}`}>
    <div><small>FPS</small><b>{hud.fps || "—"}</b></div>
    <div><small>LATENCY</small><b>{hud.visible?`${hud.latencyMs}ms`:"—"}</b></div>
    <div><small>LOCK</small><b className={hud.visible?"ok":"warn"}>{hud.visible?"TRACKING":"SEARCHING"}</b></div>
    <div><small>UPLINK</small><b className={hud.streaming?"ok":"dim"}>{endpoint?(hud.streaming?"LIVE":"IDLE"):"OFF"}</b></div>
   </div>
   {positions&&hud.visible&&chips.map(([label,key])=><JointChip key={key} label={label} pos={positions[key]} angleDeg={joints?joints[key]:90} visible={hud.visible}/>)}
   <span className="camStatus">{status}</span>
  </div>
  <div className="angleGrid">
   {chips.map(([label,key])=><div key={key}><small>{label}</small><b>{joints?`${joints[key]}°`:"—"}</b></div>)}
  </div>
  <div className="mirrorButtons"><button onClick={()=>running?stop():start()}>{running?"STOP MIRROR":"START CAMERA"}</button></div>
 </section>
 <section className="panel"><h2>Robot connection</h2><label>HTTPS mirror endpoint<input value={endpoint} onChange={e=>setEndpoint(e.target.value)} placeholder="https://your-robot.example.com/mirror_pose"/></label><label>Bearer token<input type="password" value={token} onChange={e=>setToken(e.target.value)} placeholder="Same DASHBOARD_TOKEN as the Pi"/></label><button className="outline" onClick={save}>SAVE MIRROR SETTINGS</button><p className="note">On the Pi set ENABLE_MIRROR_CONTROL=1, DASHBOARD_TOKEN, and MIRROR_ALLOWED_ORIGIN. A public HTTPS page cannot call an insecure HTTP robot endpoint; use HTTPS or run the dashboard locally.</p></section>
 </main>;
}
