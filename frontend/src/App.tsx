import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, WorkbenchApiClient } from "./api/client";
import type { CatalogArtifactRecord, ExperimentJobRecord, HealthResponse, RuntimeModelsResponse } from "./api/types";
import { AppShell } from "./components/AppShell";
import { ConnectionPanel } from "./components/ConnectionPanel";
import { OverviewPage } from "./features/overview/OverviewPage";

type DataState = "disconnected" | "loading" | "ready" | "error";

function safeMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "The local API request failed.";
  if (error.status === 401 || error.code === "authentication_required") return "Authentication failed. Check the local bearer token.";
  if (error.kind === "network") return "Could not reach the local API. Confirm that the backend is running.";
  if (error.code === "job_execution_unconfigured") return "The backend job manager is not configured.";
  if (error.kind === "non_json" || error.kind === "malformed_response") return "The backend returned an unexpected response.";
  return `The API request failed (${error.code}).`;
}

export default function App() {
  const [token, setToken] = useState<string | null>(null);
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

  const connect = useCallback(async (candidate: string): Promise<boolean> => {
    setConnectionChecking(true);
    setConnectionError(null);
    try {
      const verifiedModels = await new WorkbenchApiClient({ token: candidate }).getRuntimeModels();
      setRuntimeModels(verifiedModels);
      setToken(candidate);
      return true;
    } catch (error) {
      setConnectionError(safeMessage(error));
      return false;
    } finally {
      setConnectionChecking(false);
    }
  }, []);

  const disconnect = useCallback(() => {
    setToken(null);
    setRuntimeModels(null);
    setConnectionError(null);
    setJobs([]);
    setEvaluationArtifacts([]);
    setAblationArtifacts([]);
    setDataError(null);
    setDataState("disconnected");
  }, []);

  const refresh = useCallback(() => setRefreshVersion((version) => version + 1), []);

  useEffect(() => {
    let active = true;
    setHealth(null);
    setHealthError(null);
    setRefreshing(true);
    setDataError(null);
    setDataState(token ? "loading" : "disconnected");

    void client.getHealth().then((value) => {
      if (active) setHealth(value);
    }).catch((error: unknown) => {
      if (active) setHealthError(safeMessage(error));
    });

    if (token) {
      const requests = Promise.all([
        client.getRuntimeModels(),
        client.listJobs({ limit: 10 }),
        client.listArtifacts({ artifact_type: "evaluation_result", limit: 10 }),
        client.listArtifacts({ artifact_type: "ablation_result", limit: 10 }),
      ]);
      void requests.then(([models, recentJobs, evaluations, ablations]) => {
        if (!active) return;
        setRuntimeModels(models);
        setJobs(recentJobs);
        setEvaluationArtifacts(evaluations);
        setAblationArtifacts(ablations);
        setDataState("ready");
      }).catch((error: unknown) => {
        if (!active) return;
        setDataError(safeMessage(error));
        setDataState("error");
      }).finally(() => {
        if (active) setRefreshing(false);
      });
    } else {
      setRefreshing(false);
    }

    return () => { active = false; };
  }, [client, refreshVersion, token]);

  return (
    <AppShell>
      <ConnectionPanel
        connected={token !== null}
        checking={connectionChecking}
        runtimeModels={runtimeModels}
        error={connectionError}
        onConnect={connect}
        onDisconnect={disconnect}
      />
      <OverviewPage
        connected={token !== null}
        health={health}
        healthError={healthError}
        dataState={dataState}
        jobs={jobs}
        evaluationArtifacts={evaluationArtifacts}
        ablationArtifacts={ablationArtifacts}
        dataError={dataError}
        refreshing={refreshing}
        onRefresh={refresh}
      />
    </AppShell>
  );
}
