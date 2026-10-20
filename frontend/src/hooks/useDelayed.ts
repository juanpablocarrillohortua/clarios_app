// A flag that turns on only after `on` has stayed on for `delay` milliseconds,
// and off at once. A wait that ends quickly then shows nothing at all: no bar
// or indicator flashes for a request that answers fast.
import { useEffect, useState } from "react";

export function useDelayed(on: boolean, delay: number): boolean {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (!on) {
      setShown(false);
      return;
    }
    const timer = window.setTimeout(() => setShown(true), delay);
    return () => window.clearTimeout(timer);
  }, [on, delay]);
  return shown;
}
