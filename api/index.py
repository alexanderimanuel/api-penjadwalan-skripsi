import os
import json
import time
import uuid
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Dict, Any

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from cp_construct import load as load_model, solve as cp_solve
from sa_feasible_recommendations import search as sa_search

app = FastAPI(title="School Timetabling API (CP-SAT + SA) - Async Version")

# Dictionary sederhana di memori untuk menyimpan hasil (karena ini gratisan)
jobs = {}

class OptimizeRequest(BaseModel):
    cp_seed: int = 7
    sa_seed: int = 42
    cp_timelimit: int = 60
    sa_budget: int = 50000
    config_overrides: Dict[str, Any] = {}

def process_optimization(job_id: str, req: OptimizeRequest):
    start_time = time.perf_counter()
    try:
        jobs[job_id] = {"status": "processing", "message": "Fase 1: Menjalankan CP-SAT..."}
        
        model = load_model()
        cp_res = cp_solve(model, seed=req.cp_seed, timelimit=req.cp_timelimit, workers=1)
        
        if cp_res['status'] not in ('OPTIMAL', 'FEASIBLE'):
            jobs[job_id] = {"status": "error", "message": "CP-SAT gagal menemukan solusi H=0."}
            return
            
        placement = {eid: tuple(p) for eid, p in cp_res['placement'].items()}
        
        jobs[job_id] = {"status": "processing", "message": "Fase 2: Menjalankan SA Polish..."}
        sa_config = {
            "budget": req.sa_budget, 
            "method_version": "api-v1",
            **req.config_overrides
        }
        
        sa_result = sa_search(model, (placement, req.cp_seed), sa_config, req.sa_seed)
        runtime = time.perf_counter() - start_time
        
        jobs[job_id] = {
            "status": "completed",
            "runtime_seconds": runtime,
            "best_H": sa_result["best_feasible_quality"]["H"],
            "best_S": sa_result["best_feasible_quality"]["S"],
            "best_feasible": sa_result["best_feasible"],
            "search_evaluations": sa_result["search_evaluations"]
        }
    except Exception as e:
        jobs[job_id] = {"status": "error", "message": str(e)}

@app.get("/")
def read_root():
    return {"message": "Async CP-SAT + SA Server is running!"}

@app.post("/api/optimize/start")
def start_optimize(req: OptimizeRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "queued"}
    # Menjalankan fungsi berat di latar belakang (bebas dari timeout 100 detik!)
    background_tasks.add_task(process_optimization, job_id, req)
    return {"message": "Proses dimulai", "job_id": job_id}

@app.get("/api/optimize/status/{job_id}")
def check_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job ID tidak ditemukan")
    return jobs[job_id]
