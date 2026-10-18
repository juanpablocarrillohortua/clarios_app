// Sends one file as the body of a request, reporting its progress. The only
// module of the app that uses XMLHttpRequest: fetch has no upload progress.
//
// The file goes as the raw body (not a form), so the server streams it to
// storage as it arrives. The browser sets Content-Length from the file. The
// file's name travels percent-encoded, because a header holds ASCII only.

/** How much of a file has been sent. */
export interface SendProgress {
  sent: number;
  total: number;
}

export interface SendFile {
  /** Where to send it, for example "/api/drafts?dataset_key=…". */
  url: string;
  file: File;
  /** The access token, asked for just before sending. */
  token: string;
  /** Called as the bytes leave the browser. */
  onProgress: (progress: SendProgress) => void;
  /** Aborting it stops the upload; the promise then rejects with SendAborted. */
  signal: AbortSignal;
}

/** The server's answer, whatever its status. */
export interface SendAnswer {
  status: number;
  body: unknown;
}

/** The user stopped the upload. */
export class SendAborted extends Error {
  constructor() {
    super("the upload was stopped");
    this.name = "SendAborted";
  }
}

/** The connection broke: no answer arrived. */
export class SendBroken extends Error {
  constructor() {
    super("the upload broke off without an answer");
    this.name = "SendBroken";
  }
}

const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

function parse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export function sendFile({ url, file, token, onProgress, signal }: SendFile): Promise<SendAnswer> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new SendAborted());
      return;
    }
    const request = new XMLHttpRequest();
    const stop = () => request.abort();

    request.open("POST", url);
    request.setRequestHeader("Authorization", `Bearer ${token}`);
    request.setRequestHeader("Content-Type", file.type || XLSX);
    request.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
    request.setRequestHeader("Accept", "application/json");

    request.upload.onprogress = (event) => {
      onProgress({ sent: event.loaded, total: event.lengthComputable ? event.total : file.size });
    };
    request.onload = () => {
      signal.removeEventListener("abort", stop);
      resolve({ status: request.status, body: parse(request.responseText) });
    };
    request.onerror = () => {
      signal.removeEventListener("abort", stop);
      reject(new SendBroken());
    };
    request.onabort = () => {
      signal.removeEventListener("abort", stop);
      reject(new SendAborted());
    };

    signal.addEventListener("abort", stop, { once: true });
    request.send(file);
  });
}
