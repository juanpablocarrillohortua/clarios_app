// Shows a Power BI report through Microsoft's embedding library. This is the
// only file that imports the library; everything else sees the EmbedReport
// interface.
//
// The model is "embed for your organization": the report opens with the
// signed-in user's own Entra ID token, and Power BI decides what that user may
// see. The app only views: it asks for no permission to edit or save.
import { factories, type models as Models, models, type Report, service } from "powerbi-client";

import type { EmbedReport } from "./reportEmbed";

// One service for the page. It owns the channel to every Power BI frame.
const powerBi = new service.Service(
  factories.hpmFactory,
  factories.wpmpFactory,
  factories.routerFactory,
);

export const embedPowerBiReport: EmbedReport = (options) => {
  // A configuration of type "report" gets a Report; the library types it as
  // any embed.
  const report = powerBi.embed(options.container, {
    type: "report",
    id: options.reportId,
    embedUrl: options.embedUrl,
    accessToken: options.token,
    // The user's own token, not an embed token made by a server.
    tokenType: models.TokenType.Aad,
    permissions: models.Permissions.Read,
    viewMode: models.ViewMode.View,
    eventHooks: { accessTokenProvider: options.freshToken },
    settings: {
      // The report draws its own page background, as designed: its pages are
      // dark. Made transparent, they lost it and their light text stood on
      // white (seen signed in, 2026-10-16).
      background: models.BackgroundType.Default,
      // The whole page in view, never scrolled (the owner's requirement,
      // 2026-10-16). The dashboard shapes the frame like the page, from
      // onPageSize, so the page fills it without bands.
      layoutType: models.LayoutType.Custom,
      customLayout: { displayOption: models.DisplayOption.FitToPage },
      localeSettings: { language: options.language, formatLocale: options.language },
      // None of Power BI's own panes: the report carries its own. Every page
      // has buttons to every other page and its slicers on the page, and the
      // filters in its pane are hidden from viewers (read from the report's
      // definition, 2026-10-16). See ADR 0008, decision 10.
      panes: {
        filters: { visible: false },
        pageNavigation: { visible: false },
      },
    },
  }) as Report;

  // The library gives its frame the container's size and nothing else.
  report.iframe.style.border = "0";
  report.setComponentTitle(options.title);

  report.on("loaded", () => {
    options.onLoaded();
    // The open page's size: asked once loaded, then told with every change of
    // page. A page that does not say is drawn in the frame as it stands.
    report
      .getActivePage()
      .then((page) => tellPageSize(page.defaultSize))
      .catch(() => {});
  });
  report.on<{ newPage?: Models.IPage }>("pageChanged", (event) => {
    tellPageSize(event.detail?.newPage?.defaultSize);
  });

  function tellPageSize(size: Models.ICustomPageSize | undefined) {
    if (size?.width && size.height) {
      options.onPageSize?.({ width: size.width, height: size.height });
    }
  }
  // Power BI may send the same error more than once: a text that is no token
  // was refused twice in the same instant (seen on 2026-10-13).
  report.on<Models.IError>("error", (event) => {
    // The library raises this as a DOM event named "error" that bubbles. At
    // the window it would pass for an uncaught error with whatever listens
    // there: the dev server's client reported it as one (2026-10-13).
    event.stopPropagation();
    const { message, errorCode, detailedMessage, technicalDetails } = event.detail;
    options.onError({
      message,
      detail: [errorCode, detailedMessage].filter(Boolean).join(" · ") || null,
      requestId: technicalDetails?.requestId ?? null,
    });
  });

  return {
    setToken: (token) => report.setAccessToken(token),
    remove() {
      report.off("loaded");
      report.off("error");
      report.off("pageChanged");
      powerBi.reset(options.container);
    },
  };
};
