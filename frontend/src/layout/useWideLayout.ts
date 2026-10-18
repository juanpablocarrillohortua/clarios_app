// Whether the window is wide enough for the shell's wide layout: the sections
// as tabs in the header, no sidebar and no footer, so the report gets the
// room. Below Mantine's "sm" breakpoint (768 px) the shell keeps its narrow
// layout: burger, drawer and footer.
//
// Only one layout is drawn at a time, never both hidden by CSS: the page holds
// one navigation and one of each page action.
import { useMantineTheme } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";

export function useWideLayout(): boolean {
  const theme = useMantineTheme();
  // Read on the first render, so the first paint is already the right layout.
  return useMediaQuery(`(min-width: ${theme.breakpoints.sm})`, false, {
    getInitialValueInEffect: false,
  });
}
