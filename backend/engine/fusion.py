"""Behavior-preserving lexical/semantic fusion helpers."""
from __future__ import annotations
import hashlib
import math
import re
from typing import Any
import numpy as np

class FusionError(ValueError): pass

class FusionEngine:
    def __init__(self, config: Any): self.config = config
    @staticmethod
    def classify_query_type(query: str) -> str:
        q = query.lower().strip(); words = q.split()
        if len(re.findall(r"\d+", q)) / max(len(words), 1) >= .10 or len(words) <= 6: return "keyword"
        if any(t in q for t in ("why", "how", "explain", "describe", "compare", "analyze", "summarize")): return "semantic"
        return "balanced"

    _classify_query_type = classify_query_type
    @staticmethod
    def normalize_scores(scores: list[float], method: str = "minmax") -> list[float]:
        if not scores: return []
        a=np.asarray(scores,dtype=float)
        if method == "minmax":
            lo, hi=float(a.min()), float(a.max()); return [1/len(a)]*len(a) if hi == lo else ((a-lo)/(hi-lo)).tolist()
        if method == "zscore":
            sd=float(a.std()); return [0.5]*len(a) if sd == 0 else ((a-a.mean())/sd).tolist()
        e=np.exp(a-np.max(a)); return (e/e.sum()).tolist()

    _normalize_scores = normalize_scores
    @staticmethod
    def bm25_distances(scores: list[float]) -> list[list[float]]:
        if not scores: return [[]]
        mx=max(scores)
        return [[1.0]*len(scores)] if mx <= 0 else [[max(0.0, 1-score/(mx*1.2)) for score in scores]]

    _bm25_distances = bm25_distances
    @staticmethod
    def calculate_overlap(ids1: list[str], ids2: list[str]) -> dict[str, Any]:
        a,b=set(ids1),set(ids2); inter=a&b; union=a|b
        return {"intersection_size":len(inter),"union_size":len(union),"jaccard_similarity":len(inter)/len(union) if union else 0.0,"overlap_percentage":len(inter)/len(a)*100 if a else 0.0,"intersection_ids":sorted(inter)}

    _calculate_overlap = calculate_overlap
    def adaptive_weights(self, query: str, bm25_results: dict[str, Any]) -> tuple[float,float,dict[str,Any]]:
        if not self.config.adaptive_weighting: return self.config.bm25_weight,self.config.semantic_weight,{"query_type":self.classify_query_type(query),"bm25_signal_strength":0.0,"lexical_query_signal":0.0,"fallback_to_semantic":False,"fallback_reason":"","weight_adjusted":False}
        typ=self.classify_query_type(query); st=bm25_results.get("bm25_stats",{}); mx=float(st.get("max_score",0)); sd=float(st.get("std_score",0)); cov=float(st.get("corpus_coverage",0)); nz=int(st.get("non_zero_docs",0)); words=query.lower().split(); nr=len(re.findall(r"\d+",query.lower()))/max(len(words),1); lex=max(max(0,1-min(len(words)/12,1)),min(nr/.25,1)); maxsig=min(mx/max(self.config.bm25_strong_max_score_threshold*4,1),1); sdsig=min(sd/max(self.config.bm25_strong_std_threshold*3,1),1); strength=self.config.bm25_signal_max_weight*maxsig+self.config.bm25_signal_std_weight*sdsig+self.config.bm25_signal_spec_weight*(1-min(cov,1))+self.config.bm25_signal_lex_weight*lex
        base={"query_type":typ,"bm25_signal_strength":round(strength,4),"lexical_query_signal":round(lex,4)}
        if nz==0 or mx<=0: return 0.,1.,{**base,"fallback_to_semantic":True,"fallback_reason":"no_positive_bm25_scores","weight_adjusted":True}
        preset=(self.config.adaptive_weights_keyword if typ=="keyword" else self.config.adaptive_weights_semantic if typ=="semantic" else self.config.adaptive_weights_balanced_strong_bm25 if mx>=self.config.bm25_strong_max_score_threshold or sd>=self.config.bm25_strong_std_threshold or strength>=.55 else self.config.adaptive_weights_balanced_weak_bm25 if mx<=self.config.bm25_weak_max_score_threshold or strength<=.35 else (self.config.bm25_weight,self.config.semantic_weight))
        bw=round(float(preset[0]),3); sw=round(float(preset[1]),3)
        return bw,sw,{**base,"fallback_to_semantic":False,"fallback_reason":"","weight_adjusted":abs(bw-self.config.bm25_weight)>1e-6}

    _get_adaptive_weights = adaptive_weights
    def fuse(self, bm: dict[str,Any], sem: dict[str,Any], bm_weight:float, sem_weight:float, limit:int) -> tuple[dict[str,Any],dict[str,Any]]:
        b_ids=bm.get("ids",[[]])[0]; s_ids=sem.get("ids",[[]])[0]; raw_bm=bm.get("scores",[]); raw_bm=raw_bm[0] if raw_bm and isinstance(raw_bm[0], list) else raw_bm; bn=self.normalize_scores(raw_bm); sims=[1-float(x) for x in sem.get("distances",[[]])[0]]; sn=self.normalize_scores(sims); bi={x:i for i,x in enumerate(b_ids)}; si={x:i for i,x in enumerate(s_ids)}; ids=list(dict.fromkeys(b_ids+s_ids)); rows=[]
        for x in ids:
            ib, is_ = bi.get(x), si.get(x); score=(bm_weight*(bn[ib] if ib is not None else 0))+(sem_weight*(sn[is_] if is_ is not None else 0)); rows.append((x,score,ib,is_))
        rows.sort(key=lambda r:(-r[1], ids.index(r[0]))); rows=rows[:limit]
        out={"ids":[[r[0] for r in rows]],"fused_scores":[[r[1] for r in rows]],"bm25_scores":[[raw_bm[r[2]] if r[2] is not None else 0.0 for r in rows]],"documents":[[bm.get("documents",[[]])[0][r[2]] if r[2] is not None else sem.get("documents",[[]])[0][r[3]] for r in rows]],"metadatas":[[bm.get("metadatas",[[]])[0][r[2]] if r[2] is not None else sem.get("metadatas",[[]])[0][r[3]] for r in rows]]}
        return out,{"bm25_contribution_mean":float(np.mean([bn[r[2]]*bm_weight if r[2] is not None else 0.0 for r in rows])) if rows else 0.0,"semantic_contribution_mean":float(np.mean([sn[r[3]]*sem_weight if r[3] is not None else 0.0 for r in rows])) if rows else 0.0,"bm25_only_docs":sum(sn[r[3]] == 0 if r[3] is not None else True for r in rows),"semantic_only_docs":sum(bn[r[2]] == 0 if r[2] is not None else True for r in rows),"both_signals_docs":sum(r[2] is not None and r[3] is not None and bn[r[2]] > 0 and sn[r[3]] > 0 for r in rows)}

    def _fuse_scores(self, bm25_results: dict[str, Any], semantic_results: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        return self.fuse(bm25_results, semantic_results, self.config.bm25_weight, self.config.semantic_weight, 20)

    def _rrf_fusion(self, bm25_results: dict[str, Any], semantic_results: dict[str, Any], k_rrf: int | None = None, bm25_weight: float | None = None, semantic_weight: float | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        return self.rrf(bm25_results, semantic_results, float(self.config.bm25_weight if bm25_weight is None else bm25_weight), float(self.config.semantic_weight if semantic_weight is None else semantic_weight), 20, k_rrf)
    def rrf(self,bm:dict[str,Any],sem:dict[str,Any],bw:float,sw:float,limit:int,k_rrf: int | None = None)->tuple[dict[str,Any],dict[str,Any]]:
        bi={x:i for i,x in enumerate(bm.get("ids",[[]])[0])}; si={x:i for i,x in enumerate(sem.get("ids",[[]])[0])}; ids=list(dict.fromkeys(list(bi)+list(si))); k=self.config.rrf_k_constant if k_rrf is None else k_rrf; rows=[]
        for x in ids:
            i,j=bi.get(x),si.get(x); rows.append((x,(bw/(k+i+1) if i is not None else 0)+(sw/(k+j+1) if j is not None else 0),i,j))
        rows.sort(key=lambda r:(-r[1],ids.index(r[0]))); rows=rows[:limit]; return {"ids":[[r[0] for r in rows]],"fused_scores":[[r[1] for r in rows]],"bm25_scores":[[bm.get("scores",[])[r[2]] if r[2] is not None else 0.0 for r in rows]],"documents":[[bm.get("documents",[[]])[0][r[2]] if r[2] is not None else sem.get("documents",[[]])[0][r[3]] for r in rows]],"metadatas":[[bm.get("metadatas",[[]])[0][r[2]] if r[2] is not None else sem.get("metadatas",[[]])[0][r[3]] for r in rows]]},{"bm25_only_docs":sum(r[3] is None for r in rows),"semantic_only_docs":sum(r[2] is None for r in rows),"both_signals_docs":sum(r[2] is not None and r[3] is not None for r in rows)}
