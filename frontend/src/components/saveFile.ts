// Hand a file the API sent to the browser, to be saved where the person
// chooses. The file is in memory already; nothing is written anywhere else.
export function saveFile(blob: Blob, filename: string) {
  const address = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = address;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(address);
}
