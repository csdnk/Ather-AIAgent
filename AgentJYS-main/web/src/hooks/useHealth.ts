import { useEffect, useState } from "react";
import { getSystemStatus } from "../api/health";
import type { SystemStatusResponse } from "../api/types";

export function useHealth(refreshMs = 5000) {
  const [data, setData] = useState<SystemStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      try {
        const next = await getSystemStatus();
        if (active) {
          setData(next);
          setError(null);
        }
      } catch (exc) {
        if (active) setError(exc instanceof Error ? exc : new Error(String(exc)));
      } finally {
        if (active) setLoading(false);
      }
    }

    void load();
    const timer = window.setInterval(() => void load(), refreshMs);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [refreshMs]);

  return { data, loading, error };
}
