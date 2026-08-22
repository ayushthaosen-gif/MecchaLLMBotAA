"use client";
import {useEffect,useRef,useState} from "react";
import {DrawingUtils,FilesetResolver,PoseLandmarker} from "@mediapipe/tasks-vision";

type Joints={right_shoulder:number;right_elbow:number;left_shoulder:number;left_elbow:number};
const clamp=(v:number,a=15,b=165)=>Math.max(a,Math.min(b,v));
function angle(a:{x:number;y:number},b:{x:number;y:number},c:{x:number;y:number}){
 const u={x:a.x-b.x,y:a.y-b.y},v={x:c.x-b.x,y:c.y-b.y};
 const d=Math.hypot(u.x,u.y)*Math.hypot(v.x,v.y);return d?Math.acos(clamp((u.x*v.x+u.y*v.y)/d,-1,1))*180/Math.PI:90;
}
function side(l:any[],shoulder:number,elbow:number,wrist:number,hip:number){
 const elevation=angle(l[hip],l[shoulder],l[elbow]);
 return {shoulder:clamp(30+elevation*2/3),elbow:clamp(angle(l[shoulder],l[elbow],l[wrist]))};
}
export default function MirrorPage(){
 const video=useRef<HTMLVideoElement>(null),canvas=useRef<HTMLCanvasElement>(null),tracker=useRef<PoseLandmarker|null>(null),raf=useRef(0),lastSent=useRef(0),sending=useRef(false),smooth=useRef<Joints|null>(null);
 const [running,setRunning]=useState(false),[status,setStatus]=useState("Camera stopped"),[endpoint,setEndpoint]=useState(""),[token,setToken]=useState("");
 useEffect(()=>{const chat=localStorage.getItem("meccanoid.endpoint")||"";setEndpoint(localStorage.getItem("meccanoid.mirrorEndpoint")||chat.replace(/\/chat\/?$/,"/mirror_pose"));setToken(localStorage.getItem("meccanoid.token")||"");return()=>{stop(false);tracker.current?.close()}},[]);
 function stop(report=true){cancelAnimationFrame(raf.current);video.current?.srcObject instanceof MediaStream&&video.current.srcObject.getTracks().forEach(t=>t.stop());setRunning(false);if(report)setStatus("Camera stopped — robot auto-rests within 750 ms.")}
 async function start(){
  try{setStatus("Loading on-device pose model…");const vision=await FilesetResolver.forVisionTasks("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.22-rc.20250304/wasm");
   tracker.current??=await PoseLandmarker.createFromOptions(vision,{baseOptions:{modelAssetPath:"https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",delegate:"GPU"},runningMode:"VIDEO",numPoses:1,minPoseDetectionConfidence:.6,minTrackingConfidence:.6});
   const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:"user",width:{ideal:960},height:{ideal:720}},audio:false});if(!video.current)return;video.current.srcObject=stream;await video.current.play();setRunning(true);setStatus("Tracking locally");loop();
  }catch(e){stop(false);setStatus(e instanceof Error?e.message:"Unable to start camera")}
 }
 function loop(){
  const v=video.current,c=canvas.current,t=tracker.current;if(!v||!c||!t||v.readyState<2){raf.current=requestAnimationFrame(loop);return}
  c.width=v.videoWidth;c.height=v.videoHeight;const ctx=c.getContext("2d")!;ctx.clearRect(0,0,c.width,c.height);
  const result=t.detectForVideo(v,performance.now());if(result.landmarks[0]&&[11,12,13,14,15,16,23,24].every(i=>(result.landmarks[0][i].visibility??1)>.45)){const l=result.landmarks[0];new DrawingUtils(ctx).drawConnectors(l,PoseLandmarker.POSE_CONNECTIONS,{color:"#4fd8e0",lineWidth:3});
   // Front-camera mirror: the person's left arm drives the robot's right arm.
   const right=side(l,11,13,15,23),left=side(l,12,14,16,24);const raw:Joints={right_shoulder:right.shoulder,right_elbow:right.elbow,left_shoulder:left.shoulder,left_elbow:left.elbow};
   const prev=smooth.current||raw;const joints=Object.fromEntries(Object.entries(raw).map(([k,x])=>[k,Math.round((prev as any)[k]*.7+x*.3)])) as Joints;smooth.current=joints;
   if(endpoint&&performance.now()-lastSent.current>100&&!sending.current){lastSent.current=performance.now();sending.current=true;fetch(endpoint,{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:`Bearer ${token}`}:{})},body:JSON.stringify({joints})}).then(r=>{if(!r.ok)throw new Error(`Mirror endpoint HTTP ${r.status}`)}).catch(e=>setStatus(e.message)).finally(()=>{sending.current=false})}
  }else setStatus("Move back until shoulders, elbows and wrists are visible");
  raf.current=requestAnimationFrame(loop);
 }
 function save(){localStorage.setItem("meccanoid.mirrorEndpoint",endpoint.trim());setStatus(endpoint?"Mirror endpoint saved":"Wireframe-only mode")}
 return <main className="mirrorPage"><header><a href="/">← Console</a><div className="brand"><b>MECCANOID</b> // POSE MIRROR</div></header><section className="panel"><h1>Copy my arm movements</h1><p className="note">Your camera is processed on this device. Only four joint angles are transmitted; video never goes to the robot or cloud.</p><div className="camera"><video ref={video} playsInline muted/><canvas ref={canvas}/><span>{status}</span></div><div className="mirrorButtons"><button onClick={running?stop:start}>{running?"STOP MIRROR":"START CAMERA"}</button></div></section><section className="panel"><h2>Robot connection</h2><label>HTTPS mirror endpoint<input value={endpoint} onChange={e=>setEndpoint(e.target.value)} placeholder="https://your-robot.example.com/mirror_pose"/></label><label>Bearer token<input type="password" value={token} onChange={e=>setToken(e.target.value)} placeholder="Same DASHBOARD_TOKEN as the Pi"/></label><button className="outline" onClick={save}>SAVE MIRROR SETTINGS</button><p className="note">On the Pi set ENABLE_MIRROR_CONTROL=1, DASHBOARD_TOKEN, and MIRROR_ALLOWED_ORIGIN. A public HTTPS page cannot call an insecure HTTP robot endpoint; use HTTPS or run the dashboard locally.</p></section></main>
}