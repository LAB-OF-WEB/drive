import JSZip from "jszip"
import { toast } from "./toasts"
import { printDoc } from "./files"
import emitter from "@/emitter"
import router from "@/router"
import html2pdf from "html2pdf.js"
import editorStyle from "@/components/DocEditor/styles/editor.css?inline"
import globalStyle from "@/index.css?inline"

// Renders a Drive-themed error toast. Prefers the frappe exception message
// (error.messages[0]) and falls back to a friendly message for network /
// system / server failures so users never see a silent or cryptic error.
function showDownloadError(error, fallback) {
  let message = fallback
  const raw = error?.message || error?.messages?.join(" ") || ""
  if (error?.messages?.length) {
    message = error.messages[0]
  } else if (raw) {
    message = raw
  }
  if (/failed to fetch|networkerror|load failed|offline|aborted/i.test(raw)) {
    message = "You're offline. Check your internet connection and try again."
  } else if (/^5\d\d\b|internal server|service unavailable/i.test(raw)) {
    message = "Something went wrong on the server. Please try again later."
  } else if (/^4\d\d\b/i.test(raw)) {
    message = "That file isn't available anymore. It may have been moved or deleted."
  }
  console.error("[Drive][Download] error:", error)
  toast({ title: message, type: "error" })
}

// Prevents starting a second download while one is already in progress.
// Set synchronously (before any async work) so rapid double-clicks cannot
// race and both begin; cleared in a finally on every download path.
let downloadInProgress = false
function isDownloadInProgress() {
  if (downloadInProgress) {
    toast({
      title: "A download is already in progress. Please wait...",
      type: "info",
    })
    return true
  }
  downloadInProgress = true
  return false
}
function clearDownloadInProgress() {
  downloadInProgress = false
}

async function getPdfFromDoc(entity_name) {
  const res = await fetch(
    `/api/method/drive.api.files.get_file_content?entity_name=${entity_name}`
  )
  const raw_html = (await res.json()).message
  const content = `
          <!DOCTYPE html>
          <html>
            <head>
              <style>${globalStyle}</style>
              <style>${editorStyle}</style>
            </head>
            <body>
              <div class="ProseMirror prose-sm" style='padding-left: 40px; padding-right: 40px; padding-top: 20px; padding-bottom: 20px; margin: 0;'>
                ${raw_html}
              </div>
            </body>
          </html>
        `

  const pdfBlob = html2pdf().from(content).toPdf()
  await pdfBlob
  return pdfBlob.prop.pdf.output("arraybuffer")
}
export function entitiesDownload(team, entities, transfer = false) {
  console.log("[Drive][Download] entitiesDownload entry", {
    team,
    count: entities.length,
    names: entities.map((e) => e && e.name),
    transfer,
  })
  if (isDownloadInProgress()) return
  if (entities.length === 1) {
    if (entities[0].mime_type === "frappe_doc") {
      console.log("[Drive][Download] branch: frappe_doc (printFile)")
      if (router.currentRoute.value.name) {
        emitter.emit("printFile")
        clearDownloadInProgress()
        return
      }
      // BROKEN
      return fetch(
        `/api/method/drive.api.files.get_file_content?entity_name=${entities[0].name}`
      )
        .then(async (data) => {
          const raw_html = (await data.json()).message
          printDoc(raw_html)
        })
        .catch((error) => {
          clearDownloadInProgress()
          showDownloadError(
            error,
            "Couldn't download this document. Please try again."
          )
        })
        .finally(() => clearDownloadInProgress())
    }
    if (entities[0].is_group) {
      console.log("[Drive][Download] branch: single folder -> folderDownload")
      return folderDownload(team, entities[0])
    }
    console.log("[Drive][Download] branch: single file -> blob download")
    const url = `/api/method/drive.api.files.get_file_content?entity_name=${
      entities[0].name
    }&trigger_download=1${transfer ? "&transfer=1" : ""}`
    return fetch(url)
      .then(async (response) => {
        if (!response.ok) {
          const body = await response.json().catch(() => null)
          throw Object.assign(
            new Error(
              body?.messages?.[0] ||
                `Failed to download "${entities[0].title}" (${response.status})`
            ),
            { status: response.status }
          )
        }
        return response.blob()
      })
      .then((blob) => {
        const downloadLink = document.createElement("a")
        downloadLink.href = URL.createObjectURL(blob)
        downloadLink.download = entities[0].title
        document.body.appendChild(downloadLink)
        downloadLink.click()
        document.body.removeChild(downloadLink)
        clearDownloadInProgress()
      })
      .catch((error) => {
        clearDownloadInProgress()
        showDownloadError(
          error,
          `Couldn't download "${entities[0].title}". Please try again.`
        )
      })
  }

  const t = toast("Preparing download...")
  const zip = new JSZip()
  console.log("[Drive][Download] branch: multi-select zip", { count: entities.length })

  const processEntity = async (entity, parentFolder) => {
    if (entity.is_group) {
      const folder = parentFolder.folder(entity.title)
      return get_children(team, entity.name).then((children) => {
        const promises = children.map((childEntity) =>
          processEntity(childEntity, folder)
        )
        return Promise.all(promises)
      })
    } else if (entity.document) {
      const content = await getPdfFromDoc(entities[0].name)
      parentFolder.file(entity.title + ".pdf", content)
    } else {
      const fileContent = await get_file_content(entity)
      parentFolder.file(entity.title, fileContent)
    }
  }

  const promises = entities.map((entity) => processEntity(entity, zip))

  Promise.all(promises)
    .then(() => {
      return zip.generateAsync({ type: "blob", streamFiles: true })
    })
    .then(async function (content) {
      console.log("[Drive][Download] zip ready, triggering browser download")
      var downloadLink = document.createElement("a")
      downloadLink.href = URL.createObjectURL(content)
      downloadLink.download = "Drive Download " + +new Date() + ".zip"

      document.body.appendChild(downloadLink)

      downloadLink.click()
      document.body.removeChild(downloadLink)
      document.getElementById(t).remove()
      clearDownloadInProgress()
    })
    .catch((error) => {
      document.getElementById(t)?.remove()
      clearDownloadInProgress()
      showDownloadError(
        error,
        "Couldn't download the selected files. Please try again."
      )
    })
}

export function folderDownload(team, root_entity) {
  const folderName = root_entity.title
  const zip = new JSZip()
  const rootFolder = zip.folder(root_entity.title)
  const t = toast("Preparing folder download...")
  console.log("[Drive][Download] folderDownload start", {
    team,
    folder: root_entity.name,
    title: root_entity.title,
  })
  temp(team, root_entity.name, rootFolder)
    .then(() => {
      return zip.generateAsync({ type: "blob", streamFiles: true })
    })
    .then((content) => {
      console.log("[Drive][Download] folder zip ready, triggering browser download")
      const downloadLink = document.createElement("a")
      downloadLink.href = URL.createObjectURL(content)
      downloadLink.download = folderName + ".zip"

      document.body.appendChild(downloadLink)
      downloadLink.click()
      document.body.removeChild(downloadLink)
      document.getElementById(t)?.remove()
      clearDownloadInProgress()
    })
    .catch((error) => {
      document.getElementById(t)?.remove()
      clearDownloadInProgress()
      showDownloadError(
        error,
        "Couldn't download this folder. Please try again."
      )
    })
}

function temp(team, entity_name, parentZip) {
  console.log("[Drive][Download] temp: listing children", { team, entity_name })
  return new Promise((resolve, reject) => {
    get_children(team, entity_name)
      .then((result) => {
        console.log("[Drive][Download] temp: got children", {
          entity_name,
          count: (result || []).length,
        })
        const promises = result.map((entity) => {
          if (entity.is_group) {
            const folder = parentZip.folder(entity.title)
            return temp(team, entity.name, folder)
          }
          if (entity.document) {
            getPdfFromDoc(entity.name).then((content) =>
              parentZip.file(entity.title + ".pdf", content)
            )
          } else {
            return get_file_content(entity).then((fileContent) => {
              parentZip.file(entity.title, fileContent)
            })
          }
        })

        Promise.all(promises)
          .then(() => {
            resolve()
          })
          .catch((error) => {
            reject(error)
          })
      })
      .catch((error) => {
        reject(error)
      })
  })
}

function get_file_content(entity) {
  const fileUrl =
    entity.src ||
    "/api/method/" +
      `drive.api.files.get_file_content?entity_name=${entity.name}&trigger_download=1`

  console.log("[Drive][Download] fetching file content", {
    name: entity.name,
    title: entity.title,
    url: fileUrl,
  })
  return fetch(fileUrl).then(async (response) => {
    if (response.ok) {
      return response.blob()
    } else if (response.status === 204) {
      console.log(response)
    } else {
      const body = await response.json().catch(() => null)
      console.error("[Drive][Download] file content request failed", {
        name: entity.name,
        status: response.status,
        statusText: response.statusText,
      })
      throw Object.assign(
        new Error(
          body?.messages?.[0] ||
            `Failed to fetch "${entity.title}" (${response.status})`
        ),
        { status: response.status }
      )
    }
  })
}

function get_children(team, entity_name) {
  // drive.api.list.files defaults to limit=20 (one page). Pass an explicit
  // high limit so folder downloads never silently truncate large folders.
  const url =
    "/api/method/" +
    `drive.api.list.files?team=${team}&entity_name=${entity_name}&limit=5000`
  console.log("[Drive][Download] listing children", { url })
  return fetch(url, {
    method: "GET",
    headers: {
      "X-Frappe-CSRF-Token": window.csrf_token,
      "Content-Type": "application/json",
      Accept: "application/json",
    },
  })
    .then(async (response) => {
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw Object.assign(
          new Error(
            body?.messages?.[0] ||
              `Failed to list files (${response.status})`
          ),
          { status: response.status }
        )
      }
      return response.json()
    })
    .then((json) => json.message)
}
