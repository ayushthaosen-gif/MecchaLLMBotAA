"use client";
import {useEffect,useRef,useState} from "react";
import {DrawingUtils,FilesetResolver,PoseLandmarker} from "@mediapipe/tasks-vision";

type Joints={right_shoulder:number;right_elbow:number;left_shoulder:number;left_elbow:number};
type Pos={x:number;y:number};
type JointPositions={right_shoulder:Pos;right_elbow:Pos;left_shoulder:Pos;left_elbow:Pos};
type LockState="searching"|"locking"|"tracking";
type LogLine={ts:string;kind:string;msg:string};
const clamp=(v:number,a=15,b=165)=>Math.max(a,Math.min(b,v));
function angle(a:{x:number;y:number},b:{x:number;y:number},c:{x:number;y:number}){
 const u={x:a.x-b.x,y:a.y-b.y},v={x:c.x-b.x,y:c.y-b.y};
 const d=Math.hypot(u.x,u.y)*Math.hypot(v.x,v.y);return d?Math.acos(clamp((u.x*v.x+u.y*v.y)/d,-1,1))*180/Math.PI:90;
}
// Shoulder elevation is measured relative to a torso reference point. The
// real hip landmark is the best reference when actually visible — but a
// typical webcam framing (sitting at a desk) usually crops the hips out
// entirely, and MediaPipe still reports a low-confidence "guess" there.
// Rather than requiring hips at all, derive a virtual torso reference
// from the head/shoulders — landmarks that are essentially always in
// frame together — by extending the nose-to-shoulder-midpoint vector
// further down. A rough proxy for "where the hips are" based on typical
// body proportions, not a measurement, but it keeps tracking working for
// any normal upper-body framing instead of only full-body shots.
function torsoReference(l:any[]){
 const nose=l[0], mid={x:(l[11].x+l[12].x)/2,y:(l[11].y+l[12].y)/2};
 const dx=mid.x-nose.x, dy=mid.y-nose.y;
 return {x:mid.x+dx*2,y:mid.y+dy*2};
}
function side(l:any[],shoulder:number,elbow:number,wrist:number,hip:number,virtualHip:{x:number;y:number}){
 const hipPoint=(l[hip].visibility??1)>.3?l[hip]:virtualHip;
 const elevation=angle(hipPoint,l[shoulder],l[elbow]);
 return {shoulder:clamp(30+elevation*2/3),elbow:clamp(angle(l[shoulder],l[elbow],l[wrist]))};
}

// Small glowing dot + crosshair at a tracked joint — drawn on the same
// (mirrored) canvas as the wireframe, so it stays pixel-locked to the
// joint. Angle text is NOT drawn here — canvas text would render
// mirror-flipped/backwards along with the video (see .camera's
// transform:scaleX(-1)); the numeric labels are separate, non-mirrored
// DOM chips positioned over the video instead (see JointChip below).
function drawJointMarker(ctx:CanvasRenderingContext2D,x:number,y:number,confident:boolean){
 ctx.save();
 ctx.shadowColor=confident?"#4fd8e0":"#c9a8ff90";ctx.shadowBlur=confident?14:8;
 ctx.strokeStyle=confident?"#4fd8e0":"#c9a8ffb0";ctx.lineWidth=1.5;
 ctx.beginPath();ctx.arc(x,y,9,0,Math.PI*2);ctx.stroke();
 ctx.beginPath();ctx.moveTo(x-14,y);ctx.lineTo(x-5,y);ctx.moveTo(x+5,y);ctx.lineTo(x+14,y);
 ctx.moveTo(x,y-14);ctx.lineTo(x,y-5);ctx.moveTo(x,y+5);ctx.lineTo(x,y+14);ctx.stroke();
 ctx.restore();
}

function JointChip({label,pos,angleDeg}:{label:string;pos:Pos;angleDeg:number}){
 // pos.x/.y are normalized [0,1] in the RAW (unmirrored) camera frame.
 // This chip is a sibling of the mirrored video/canvas, not a mirrored
 // element itself, so its own position must be flipped by hand
 // (1 - x) to land on the correct spot in the mirrored display.
 return <div className="jointChip" style={{left:`${(1-pos.x)*100}%`,top:`${pos.y*100}%`}}>
  <span className="jointChipLabel">{label}</span><span className="jointChipAngle">{Math.round(angleDeg)}°</span>
 </div>;
}

export default function MirrorPage(){
 const video=useRef<HTMLVideoElement>(null),canvas=useRef<HTMLCanvasElement>(null),tracker=useRef<PoseLandmarker|null>(null),raf=useRef(0),lastSent=useRef(0),sending=useRef(false),smooth=useRef<Joints|null>(null);
 const fpsRef=useRef(0),lastFrameRef=useRef(performance.now()),lastHudRef=useRef(0);
 const lockRef=useRef<LockState>("searching"),uplinkRef=useRef(false);
 const [running,setRunning]=useState(false),[status,setStatus]=useState("Camera stopped"),[endpoint,setEndpoint]=useState(""),[token,setToken]=useState("");
 const [hud,setHud]=useState({fps:0,latencyMs:0,lock:"searching" as LockState,streaming:false});
 const [joints,setJoints]=useState<Joints|null>(null);
 const [positions,setPositions]=useState<JointPositions|null>(null);
 const [log,setLog]=useState<LogLine[]>([{ts:"--:--:--",kind:"sys",msg:"idle"}]);
 function pushLog(kind:string,msg:string){
  const ts=new Date().toLocaleTimeString([],{hour12:false});
  setLog(v=>[...v.slice(-59),{ts,kind,msg}]);
 }
 useEffect(()=>{const chat=localStorage.getItem("meccanoid.endpoint")||"";setEndpoint(localStorage.getItem("meccanoid.mirrorEndpoint")||chat.replace(/\/chat\/?$/,"/mirror_pose"));setToken(localStorage.getItem("meccanoid.token")||"");return()=>{stop(false);tracker.current?.close()}},[]);
 function stop(report=true){cancelAnimationFrame(raf.current);video.current?.srcObject instanceof MediaStream&&video.current.srcObject.getTracks().forEach(t=>t.stop());setRunning(false);setHud(h=>({...h,lock:"searching",streaming:false}));if(report){setStatus("Camera stopped — robot auto-rests within 750 ms.");pushLog("sys","camera stopped — robot auto-rests within 750ms")}}
 async function start(){
  try{
   setStatus("Loading on-device pose model…");pushLog("sys","initializing pose engine");pushLog("net","fetching WASM runtime...");
   const vision=await FilesetResolver.forVisionTasks("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.22-rc.20250304/wasm");
   pushLog("net","WASM runtime ready");
   if(!tracker.current){
    pushLog("net","loading pose_landmarker_lite model...");
    tracker.current=await PoseLandmarker.createFromOptions(vision,{baseOptions:{modelAssetPath:"https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",delegate:"GPU"},runningMode:"VIDEO",numPoses:1,minPoseDetectionConfidence:.6,minTrackingConfidence:.6});
    pushLog("net","landmark model ready");
   }
   setStatus("Requesting camera access…");pushLog("cam","requesting camera access...");
   const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:"user",width:{ideal:960},height:{ideal:720}},audio:false});
   const settings=stream.getVideoTracks()[0]?.getSettings()||{};
   pushLog("cam",`stream acquired ${settings.width||"?"}x${settings.height||"?"}`);
   if(!video.current)return;video.current.srcObject=stream;await video.current.play();
   setRunning(true);setStatus("Tracking locally");lastFrameRef.current=performance.now();
   lockRef.current="searching";uplinkRef.current=false;
   pushLog("sys","tracking loop started");
   loop();
  }catch(e){
   const msg=e instanceof Error?e.message:"Unable to start camera";
   pushLog("err",msg);stop(false);setStatus(msg);
  }
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

  // Two separate gates, not one: whether to DRAW (loose — any pose at
  // all) vs whether it's confident enough to COMPUTE + STREAM angles to
  // a real robot (stricter — arm joints specifically). Requiring all 8
  // of shoulders/elbows/wrists/HIPS visible before drawing anything
  // meant a typical webcam framing (sitting at a desk, hips out of
  // frame) never rendered a wireframe at all, even though the arms were
  // being tracked fine. Hips are only needed for the elevation formula
  // below, not to decide whether to show anything.
  const poseDetected=!!result.landmarks[0];
  const armsConfident=poseDetected&&[11,12,13,14,15,16].every(i=>(result.landmarks[0][i].visibility??1)>.45);
  let liveJoints:Joints|null=null,livePositions:JointPositions|null=null;
  let lock:LockState="searching";

  if(poseDetected){
   const l=result.landmarks[0];
   new DrawingUtils(ctx).drawConnectors(l,PoseLandmarker.POSE_CONNECTIONS,{color:armsConfident?"#4fd8e0aa":"#4fd8e055",lineWidth:2});
   // Front-camera mirror: the person's left arm drives the robot's right arm.
   const vHip=torsoReference(l);
   const right=side(l,11,13,15,23,vHip),left=side(l,12,14,16,24,vHip);
   const raw:Joints={right_shoulder:right.shoulder,right_elbow:right.elbow,left_shoulder:left.shoulder,left_elbow:left.elbow};
   const prev=smooth.current||raw;const smoothed=Object.fromEntries(Object.entries(raw).map(([k,x])=>[k,Math.round((prev as any)[k]*.7+x*.3)])) as Joints;
   smooth.current=smoothed;liveJoints=smoothed;
   livePositions={right_shoulder:{x:l[11].x,y:l[11].y},right_elbow:{x:l[13].x,y:l[13].y},left_shoulder:{x:l[12].x,y:l[12].y},left_elbow:{x:l[14].x,y:l[14].y}};
   for(const p of Object.values(livePositions))drawJointMarker(ctx,p.x*c.width,p.y*c.height,armsConfident);

   lock=armsConfident?"tracking":"locking";
   if(lock!==lockRef.current){
    if(lock==="tracking")pushLog("track","arm tracking lock confirmed");
    else if(lockRef.current==="searching")pushLog("track","pose acquired, locking arm joints...");
   }

   if(armsConfident&&endpoint&&now-lastSent.current>100&&!sending.current){
    lastSent.current=now;sending.current=true;
    if(!uplinkRef.current)pushLog("uplink",`connecting to ${endpoint}`);
    fetch(endpoint,{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:`Bearer ${token}`}:{})},body:JSON.stringify({joints:smoothed})})
     .then(r=>{if(!r.ok)throw new Error(`Mirror endpoint HTTP ${r.status}`);if(!uplinkRef.current){pushLog("uplink","first frame acknowledged");uplinkRef.current=true}})
     .catch(e=>{setStatus(e.message);pushLog("err",e.message);uplinkRef.current=false})
     .finally(()=>{sending.current=false});
   }
   setStatus(armsConfident?"Tracking locally":"Pose seen — move shoulders/elbows/wrists fully into frame");
  }else{
   setStatus("No pose detected — step into frame");
   if(lockRef.current!=="searching")pushLog("track","pose lost — searching");
   uplinkRef.current=false;
  }
  lockRef.current=lock;

  // "Streaming" reads straight off lastSent.current (a ref, always
  // current) rather than the hud state — loop() recurses via its own
  // requestAnimationFrame call, not through React re-renders, so a
  // captured `hud` value here would just be whatever it was on the
  // very first call and never update (a stale-closure trap).
  const streaming=!!endpoint&&armsConfident&&(now-lastSent.current<300);

  // React state (and therefore the DOM/HUD) is throttled to ~8/s —
  // plenty smooth for numbers changing under smoothed tracking, and
  // far cheaper than re-rendering on every one of the ~60 canvas
  // frames/sec the wireframe itself draws at.
  if(now-lastHudRef.current>120){
   lastHudRef.current=now;
   setHud({fps:Math.round(fpsRef.current),latencyMs:Math.round(latencyMs),lock,streaming});
   setJoints(liveJoints);
   setPositions(livePositions);
  }
  raf.current=requestAnimationFrame(loop);
 }
 function save(){localStorage.setItem("meccanoid.mirrorEndpoint",endpoint.trim());setStatus(endpoint?"Mirror endpoint saved":"Wireframe-only mode")}
 const chips:[string,keyof Joints][]=[["R-SHOULDER","right_shoulder"],["R-ELBOW","right_elbow"],["L-SHOULDER","left_shoulder"],["L-ELBOW","left_elbow"]];
 const lockLabel={searching:"SEARCHING",locking:"LOCKING",tracking:"TRACKING"}[hud.lock];
 const lockClass={searching:"warn",locking:"partial",tracking:"ok"}[hud.lock];
 return <main className="mirrorPage"><header><a href="/">← Console</a><div className="brand"><b>MECCANOID</b> // POSE MIRROR</div></header>
 <section className="panel"><h1>Copy my arm movements</h1><p className="note">Your camera is processed on this device. Only four joint angles are transmitted; video never goes to the robot or cloud.</p>
  <div className="camera">
   <video ref={video} playsInline muted/><canvas ref={canvas}/>
   <div className="hudCorners"><i/><i/><i/><i/></div>
   <div className={`hudReadout${hud.lock!=="searching"?" live":""}`}>
    <div><small>FPS</small><b>{hud.fps || "—"}</b></div>
    <div><small>LATENCY</small><b>{hud.lock!=="searching"?`${hud.latencyMs}ms`:"—"}</b></div>
    <div><small>LOCK</small><b className={lockClass}>{lockLabel}</b></div>
    <div><small>UPLINK</small><b className={hud.streaming?"ok":"dim"}>{endpoint?(hud.streaming?"LIVE":(hud.lock==="tracking"?"IDLE":"WAIT")):"OFF"}</b></div>
   </div>
   {positions&&chips.map(([label,key])=><JointChip key={key} label={label} pos={positions[key]} angleDeg={joints?joints[key]:90}/>)}
   <span className="camStatus">{status}</span>
  </div>
  <div className="angleGrid">
   {chips.map(([label,key])=><div key={key}><small>{label}</small><b>{joints?`${joints[key]}°`:"—"}</b></div>)}
  </div>
  <div className="mirrorButtons"><button onClick={()=>running?stop():start()}>{running?"STOP MIRROR":"START CAMERA"}</button></div>
  <div className="terminal">
   {log.map((l,i)=><div key={i} className="tline"><span className="t-ts">[{l.ts}]</span> <span className={`t-${l.kind}`}>{l.kind.toUpperCase()}</span> :: {l.msg}</div>)}
   <div className="tline"><span className="term-cursor"/></div>
  </div>
 </section>
 <section className="panel"><h2>Robot connection</h2><label>HTTPS mirror endpoint<input value={endpoint} onChange={e=>setEndpoint(e.target.value)} placeholder="https://your-robot.example.com/mirror_pose"/></label><label>Bearer token<input type="password" value={token} onChange={e=>setToken(e.target.value)} placeholder="Same DASHBOARD_TOKEN as the Pi"/></label><button className="outline" onClick={save}>SAVE MIRROR SETTINGS</button><p className="note">On the Pi set ENABLE_MIRROR_CONTROL=1, DASHBOARD_TOKEN, and MIRROR_ALLOWED_ORIGIN. A public HTTPS page cannot call an insecure HTTP robot endpoint; use HTTPS or run the dashboard locally.</p></section>
 </main>;
}
