import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, Route, Routes, useLocation } from "react-router-dom";
import { BootstrapApiClient, WorkbenchApiClient } from "./api/client";
import type { BootstrapStatusResponse, CatalogArtifactRecord, ExperimentJobRecord, HealthResponse, RuntimeModelsResponse } from "./api/types";
import { AppShell } from "./components/AppShell";
import { apiErrorText } from "./components/apiErrorText";
import { ConnectionPanel } from "./components/ConnectionPanel";
import { OverviewPage } from "./features/overview/OverviewPage";
import { JobDetailPage } from "./features/jobs/JobDetailPage";
import { JobsPage } from "./features/jobs/JobsPage";
import { ArtifactDetailPage } from "./features/artifacts/ArtifactDetailPage";
import { ArtifactsPage } from "./features/artifacts/ArtifactsPage";
import { ExperimentWorkflowPage } from "./features/experiments/ExperimentWorkflowPage";
import { IndexBuildsPage } from "./features/index-builds/IndexBuildsPage";

type DataState = "disconnected" | "loading" | "ready" | "error";

export default function App() {
  const [token, setToken] = useState<string | null>(null);
  const [bootstrapToken, setBootstrapToken] = useState<string | null>(null);
  const [bootstrapStatus, setBootstrapStatus] = useState<BootstrapStatusResponse | null>(null);
  const [bootstrapChecking, setBootstrapChecking] = useState(false);
  const [bootstrapConnectionError, setBootstrapConnectionError] = useState<string | null>(null);
  const bootstrapConnectSerial = useRef(0);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [runtimeModels, setRuntimeModels] = useState<RuntimeModelsResponse | null>(null);
  const [connectionChecking, setConnectionChecking] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [dataState, setDataState] = useState<DataState>("disconnected");
  const [jobs, setJobs] = useState<ExperimentJobRecord[]>([]);
  const [evaluationArtifacts, setEvaluationArtifacts] = useState<CatalogArtifactRecord[]>([]);
  const [ablationArtifacts, setAblationArtifacts] = useState<CatalogArtifactRecord[]>([]);
  const [dataError, setDataError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const client = useMemo(() => new WorkbenchApiClient({ token }), [token]);
  const location = useLocation();
  const connected = token !== null;

  const connect = useCallback(async (candidate: string): Promise<boolean> => {
    setConnectionChecking(true); setConnectionError(null);
    try {
      const verifiedModels = await new WorkbenchApiClient({ token: candidate }).getRuntimeModels();
      setRuntimeModels(verifiedModels); setToken(candidate);
      return true;
    } catch (error) {
      setConnectionError(apiErrorText(error));
      return false;
    } finally { setConnectionChecking(false); }
  }, []);

  const disconnect = useCallback(() => {
    setToken(null); setRuntimeModels(null); setConnectionError(null);
    setJobs([]); setEvaluationArtifacts([]); setAblationArtifacts([]); setDataError(null); setDataState("disconnected");
  }, []);

  const connectBootstrap = useCallback(async (candidate: string): Promise<boolean> => {
    const serial = ++bootstrapConnectSerial.current;
    setBootstrapChecking(true); setBootstrapConnectionError(null);
    try {
      const verified = await new BootstrapApiClient({ token: candidate }).getStatus();
      if (serial !== bootstrapConnectSerial.current) return false;
      setBootstrapStatus(verified); setBootstrapToken(candidate);
      return true;
    } catch (error) {
      if (serial === bootstrapConnectSerial.current) setBootstrapConnectionError(apiErrorText(error));
      return false;
    } finally {
      if (serial === bootstrapConnectSerial.current) setBootstrapChecking(false);
    }
  }, []);

  const disconnectBootstrap = useCallback(() => {
    bootstrapConnectSerial.current += 1;
    setBootstrapToken(null); setBootstrapStatus(null); setBootstrapConnectionError(null); setBootstrapChecking(false);
  }, []);

  const refresh = useCallback(() => setRefreshVersion((version) => version + 1), []);

  useEffect(() => {
    let active = true;
    setHealth(null); setHealthError(null);
    void client.getHealth().then((value) => { if (active) setHealth(value); }).catch((error: unknown) => {
      if (active) setHealthError(apiErrorText(error));
    });
    return () => { active = false; };
  }, [client, refreshVersion]);

  useEffect(() => {
    let active = true;
    if (!connected) {
      setJobs([]); setEvaluationArtifacts([]); setAblationArtifacts([]); setDataError(null); setDataState("disconnected"); setRefreshing(false);
      return () => { active = false; };
    }
    if (location.pathname !== "/") {
      setJobs([]); setEvaluationArtifacts([]); setAblationArtifacts([]); setDataError(null); setDataState("disconnected"); setRefreshing(false);
      return () => { active = false; };
    }
    setDataState("loading"); setRefreshing(true); setDataError(null);
    void Promise.all([
      client.getRuntimeModels(),
      client.listJobs({ limit: 10 }),
      client.listArtifacts({ artifact_type: "evaluation_result", limit: 10 }),
      client.listArtifacts({ artifact_type: "ablation_result", limit: 10 }),
    ]).then(([models, recentJobs, evaluations, ablations]) => {
      if (!active) return;
      setRuntimeModels(models); setJobs(recentJobs); setEvaluationArtifacts(evaluations); setAblationArtifacts(ablations); setDataState("ready");
    }).catch((error: unknown) => {
      if (!active) return;
      setDataError(apiErrorText(error)); setDataState("error");
    }).finally(() => { if (active) setRefreshing(false); });
    return () => { active = false; };
  }, [client, connected, location.pathname, refreshVersion]);

  return <AppShell>
    {!location.pathname.startsWith("/index-builds") ? <ConnectionPanel connected={connected} checking={connectionChecking} runtimeModels={runtimeModels} error={connectionError} onConnect={connect} onDisconnect={disconnect} /> : null}
    <Routes>
      <Route path="/" element={<OverviewPage connected={connected} health={health} healthError={healthError} dataState={dataState} jobs={jobs} evaluationArtifacts={evaluationArtifacts} ablationArtifacts={ablationArtifacts} dataError={dataError} refreshing={refreshing} onRefresh={refresh} />} />
      <Route path="/jobs" element={<JobsPage client={client} connected={connected} refreshKey={refreshVersion} onRefresh={refresh} />} />
      <Route path="/jobs/:jobId" element={<JobDetailPage key={location.pathname} client={client} connected={connected} onRefresh={refresh} />} />
      <Route path="/artifacts" element={<ArtifactsPage client={client} connected={connected} refreshKey={refreshVersion} onRefresh={refresh} />} />
      <Route path="/artifacts/:artifactId" element={<ArtifactDetailPage key={location.pathname} client={client} connected={connected} refreshKey={refreshVersion} onRefresh={refresh} />} />
      <Route path="/experiments/:workflow" element={<ExperimentWorkflowPage key={location.pathname} client={client} connected={connected} onAccepted={refresh} />} />
      <Route path="/index-builds" element={<IndexBuildsPage token={bootstrapToken} status={bootstrapStatus} checking={bootstrapChecking} connectionError={bootstrapConnectionError} onConnect={connectBootstrap} onDisconnect={disconnectBootstrap} />} />
      <Route path="/index-builds/:jobId" element={<IndexBuildsPage token={bootstrapToken} status={bootstrapStatus} checking={bootstrapChecking} connectionError={bootstrapConnectionError} onConnect={connectBootstrap} onDisconnect={disconnectBootstrap} />} />
      <Route path="*" element={<section className="panel not-found"><span className="eyebrow">ROUTE NOT FOUND</span><h1>This page is unavailable</h1><p>Use the workbench navigation to return to a supported view.</p><Link className="button button--primary" to="/">Open overview</Link></section>} />
    </Routes>
  </AppShell>;
}
