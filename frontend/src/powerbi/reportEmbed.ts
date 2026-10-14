// The app's own view of a Power BI report shown in the page. The dashboard
// depends on this interface and never on Microsoft's embedding library, so a
// test can hand the app a stand-in, and the library is named in one file only
// (powerBiReportEmbed.ts).

/** What Power BI's frame reported about a failure. */
export interface EmbedError {
  /** Power BI's name for the failure, for example "TokenExpired". */
  message: string;
  /**
   * What Power BI adds to the name, in its own words: a status and a sentence,
   * for example "403 · Fail to initialize - Could not resolve cluster". One
   * name covers several causes, and this tells them apart. Null when it gave
   * neither.
   */
  detail: string | null;
  /** The request Power BI's support can look up; null when it gave none. */
  requestId: string | null;
}

export interface ReportEmbedOptions {
  /** The element the report is drawn in. The report takes its whole size. */
  container: HTMLElement;
  reportId: string;
  /** The address Power BI gave for embedding this report. */
  embedUrl: string;
  /** The user's access token for Power BI. */
  token: string;
  /** The language of Power BI's own texts, for example "es". */
  language: string;
  /** What a screen reader calls the report's frame. */
  title: string;
  /**
   * Power BI asks for a newer token, when the one it holds is about to
   * expire. It must not reject: null means that none can be had.
   */
  freshToken: () => Promise<string | null>;
  /** The report is on screen and can be used. */
  onLoaded: () => void;
  onError: (error: EmbedError) => void;
}

export interface EmbeddedReport {
  /** Replace the token of the report on screen, without reloading it. */
  setToken(token: string): Promise<void>;
  /** Take the report off the page and stop listening to it. */
  remove(): void;
}

/** Draws the report. It throws when the embed address is not one of Power BI's. */
export type EmbedReport = (options: ReportEmbedOptions) => EmbeddedReport;

/**
 * Fetches the embedding library the first time the dashboard needs it, so a
 * user who never opens the dashboard never downloads it.
 */
export type LoadEmbedReport = () => Promise<EmbedReport>;
