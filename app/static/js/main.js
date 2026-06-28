document.addEventListener("DOMContentLoaded", () => {
    const dropZone   = document.getElementById("drop-zone");
    const fileInput  = document.getElementById("files");
    const selectBtn  = document.getElementById("select-files-btn");
    const fileList   = document.getElementById("file-list");
    const submitBtn  = document.getElementById("submit-btn");

    if (!dropZone || !fileInput) return;

    const ALLOWED = [".xlsx", ".xls", ".pdf"];

    function isAllowed(name) {
        return ALLOWED.some(ext => name.toLowerCase().endsWith(ext));
    }

    // Merge dropped/selected files into the real <input type="file"> via DataTransfer
    function mergeFiles(incoming) {
        const dt = new DataTransfer();
        for (const f of fileInput.files) dt.items.add(f);
        for (const f of incoming) {
            const alreadyAdded = Array.from(dt.files).some(x => x.name === f.name);
            if (isAllowed(f.name) && !alreadyAdded) dt.items.add(f);
        }
        fileInput.files = dt.files;
        renderList();
    }

    function renderList() {
        fileList.innerHTML = "";
        for (let i = 0; i < fileInput.files.length; i++) {
            const f  = fileInput.files[i];
            const li = document.createElement("li");
            li.innerHTML =
                `<span>${f.name} <small>(${(f.size / 1024).toFixed(1)} KB)</small></span>` +
                `<button type="button" class="remove-btn" data-i="${i}" title="Удалить">&times;</button>`;
            fileList.appendChild(li);
        }
        submitBtn.disabled = fileInput.files.length === 0;
    }

    // Single entry point for file selection — button only, no label, no dropZone click
    selectBtn.addEventListener("click", () => fileInput.click());

    // Reflect picker selection in the list
    fileInput.addEventListener("change", () => renderList());

    // Remove a file by rebuilding DataTransfer without it
    fileList.addEventListener("click", e => {
        if (!e.target.classList.contains("remove-btn")) return;
        const idx = Number(e.target.dataset.i);
        const dt  = new DataTransfer();
        for (let i = 0; i < fileInput.files.length; i++) {
            if (i !== idx) dt.items.add(fileInput.files[i]);
        }
        fileInput.files = dt.files;
        renderList();
    });

    // Drag-and-drop (visual feedback + file merge; no fileInput.click() here)
    dropZone.addEventListener("dragover",  e => { e.preventDefault(); dropZone.classList.add("drag-over"); });
    dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
    dropZone.addEventListener("drop", e => {
        e.preventDefault();
        dropZone.classList.remove("drag-over");
        mergeFiles(e.dataTransfer.files);
    });
});
