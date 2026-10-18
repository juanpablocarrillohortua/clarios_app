// Mantine in the app's palette. The colours themselves are design tokens in
// styles/tokens.css; this file only points Mantine at them, by name, so no
// colour value is written here or in any component.
import {
  Anchor,
  AppShell,
  Button,
  type CSSVariablesResolver,
  colorsTuple,
  createTheme,
  defaultVariantColorsResolver,
  NavLink,
  Title,
  Tooltip,
  type VariantColorsResolver,
} from "@mantine/core";

import classes from "./styles/mantine.module.css";

const token = (name: string) => `var(--ce-${name})`;

// Mantine's dark scale carries its text and surfaces: 0 is the brightest
// text, 9 the darkest background.
const dark = colorsTuple([
  token("text"),
  token("text-secondary"),
  token("text-muted"),
  token("text-disabled"),
  token("border"),
  token("surface-raised"),
  token("surface"),
  token("canvas-top"),
  token("canvas-bottom"),
  token("outspace"),
]);

// Mantine's semantic colours, in the dark scheme the app always uses.
const cssVariablesResolver: CSSVariablesResolver = () => ({
  variables: {},
  light: {},
  dark: {
    "--mantine-color-body": token("surface"),
    "--mantine-color-text": token("text"),
    "--mantine-color-bright": token("text"),
    "--mantine-color-dimmed": token("text-muted"),
    "--mantine-color-placeholder": token("text-muted"),
    "--mantine-color-anchor": token("link"),
    "--mantine-color-error": token("error"),
    "--mantine-color-success": token("success"),
    "--mantine-color-default": token("surface"),
    "--mantine-color-default-hover": token("hover-secondary"),
    "--mantine-color-default-color": token("text"),
    "--mantine-color-default-border": token("border"),
    "--mantine-color-disabled": token("surface-sunken"),
    "--mantine-color-disabled-color": token("text-disabled"),
    "--mantine-color-disabled-border": token("border"),
    "--mantine-primary-color-contrast": token("text-on-accent"),
    "--mantine-color-accent-text": token("accent"),
    "--mantine-color-accent-filled": token("accent"),
    "--mantine-color-accent-filled-hover": token("accent-hover"),
    "--mantine-color-accent-light": token("accent-tint"),
    "--mantine-color-accent-light-hover": token("accent-tint"),
    "--mantine-color-accent-light-color": token("accent"),
    "--mantine-color-accent-outline": token("accent"),
    "--mantine-color-accent-outline-hover": token("accent-tint"),
  },
});

// White on the pink is below AA (3.44:1), so a filled control carries the
// dark text token instead (5.71:1).
const variantColorResolver: VariantColorsResolver = (input) => {
  const colors = defaultVariantColorsResolver(input);
  return input.variant === "filled" ? { ...colors, color: token("text-on-accent") } : colors;
};

export { cssVariablesResolver };

export const theme = createTheme({
  primaryColor: "accent",
  colors: { accent: colorsTuple(token("accent")), dark },
  variantColorResolver,
  fontFamily: token("font-sans"),
  headings: { fontFamily: token("font-sans"), fontWeight: "600" },
  radius: {
    xs: token("radius-xs"),
    sm: token("radius-sm"),
    md: token("radius-md"),
    lg: token("radius-md"),
    xl: token("radius-md"),
  },
  defaultRadius: "sm",
  cursorType: "pointer",
  focusRing: "auto",
  // Mantine's own transitions stop under prefers-reduced-motion.
  respectReducedMotion: true,
  components: {
    Anchor: Anchor.extend({ classNames: { root: classes.anchor } }),
    AppShell: AppShell.extend({
      classNames: {
        root: classes.shell,
        header: classes.header,
        navbar: classes.navbar,
        main: classes.main,
        footer: classes.footer,
      },
      // The narrow-screen drawer moves on the spring (instant when motion
      // is reduced, through the token).
      vars: () => ({
        root: {
          "--app-shell-transition-duration": token("duration-spring"),
          "--app-shell-transition-timing-function": token("ease-spring"),
        },
      }),
    }),
    // "filled" written out, so a primary button carries data-variant too and
    // gets its glow: Mantine marks only a variant it was given.
    Button: Button.extend({
      classNames: { root: classes.button },
      defaultProps: { variant: "filled" },
    }),
    NavLink: NavLink.extend({ classNames: { root: classes.navLink } }),
    Title: Title.extend({ classNames: { root: classes.title } }),
    // Mantine's tooltip is light on a dark page; this one is a raised surface.
    Tooltip: Tooltip.extend({
      classNames: { tooltip: classes.tooltip },
      vars: () => ({
        tooltip: { "--tooltip-bg": token("surface-raised"), "--tooltip-color": token("text") },
      }),
    }),
  },
});
