import { useEffect, useState } from "react";

export function useSsePulse(factory: () => EventSource): number {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const source = factory();
    source.onmessage = () => setTick((v) => v + 1);
    source.onerror = () => {
      // Browser will retry EventSource connections automatically.
    };
    return () => source.close();
  }, [factory]);

  return tick;
}
