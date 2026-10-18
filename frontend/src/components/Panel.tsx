// A panel, as the report draws its own: the surface, a border, a gradient line
// on top and a faint violet halo. A tone marks what kind of message it holds;
// an accent gives a summary card one colour, as the report's KPI cards.
import { Box, type BoxProps } from "@mantine/core";
import type { ReactNode } from "react";

import classes from "../styles/app.module.css";

/** The kind of message a panel holds. The title says it too: never colour alone. */
export type PanelTone = "error" | "warning" | "info" | "success";

/** The colour of a summary card. Defined for cards to come; nothing uses it yet. */
export type PanelAccent = "pink" | "blue" | "purple" | "cyan" | "magenta" | "amber";

interface PanelProps extends BoxProps {
  tone?: PanelTone;
  accent?: PanelAccent;
  children: ReactNode;
}

export function Panel({ tone, accent, children, className, ...others }: PanelProps) {
  return (
    <Box
      className={className ? `${classes.panel} ${className}` : classes.panel}
      data-tone={tone}
      data-accent={accent}
      {...others}
    >
      {children}
    </Box>
  );
}
