import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { followNewFlowRun } from "../followNewFlowRun";

type QuickRunPayload = {
  parameters?: Record<string, unknown>;
  idempotency_key?: string;
};

export function useQuickRunLaunch(onFinished: () => void) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const launch = useMutation({
    mutationFn: async (args: { deploymentId: string; payload?: QuickRunPayload }) => {
      const created = await api.triggerDeploymentRun(args.deploymentId, args.payload);
      return followNewFlowRun(created, args.payload?.idempotency_key);
    },
    onSuccess: (outcome) => {
      onFinished();
      void queryClient.invalidateQueries({ queryKey: ["flow-runs"] });
      void queryClient.invalidateQueries({ queryKey: ["deployment-runs"] });
      if (outcome.kind === "open") {
        navigate(`/runs/${outcome.flowRunId}`, {
          state: outcome.idempotencyReplay ? { idempotencyReplay: true } : undefined
        });
        return;
      }
      setNotice(`Deployment run ${outcome.status}.`);
    }
  });
  return { launch, notice };
}
