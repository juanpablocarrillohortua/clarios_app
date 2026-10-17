// A place in the header where the open page may put an action of its own, such
// as the dashboard's link to Power BI. The shell owns the place; the page owns
// what goes in it, and takes it away when it closes.
import { createContext, type ReactNode, useContext } from "react";
import { createPortal } from "react-dom";

/** The header's element for page actions; null where the header has none. */
export const HeaderActionSlot = createContext<HTMLElement | null>(null);

/** Draws its children in the header, or nothing when the header has no place for them. */
export function HeaderAction({ children }: { children: ReactNode }) {
  const slot = useContext(HeaderActionSlot);
  return slot ? createPortal(children, slot) : null;
}
