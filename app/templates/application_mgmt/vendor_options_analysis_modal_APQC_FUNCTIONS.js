// ============================================================================
// APQC PROCESS SELECTION FUNCTIONS
// ============================================================================

function removeProcess(processId) {
    const levels = [
        typeof selectedL1Processes !== 'undefined' ? selectedL1Processes : [],
        typeof selectedL2Processes !== 'undefined' ? selectedL2Processes : [],
        typeof selectedL3Processes !== 'undefined' ? selectedL3Processes : [],
        typeof selectedL4Processes !== 'undefined' ? selectedL4Processes : [],
        typeof selectedL5Processes !== 'undefined' ? selectedL5Processes : [],
    ];
    levels.forEach(levelArray => {
        const index = levelArray.findIndex(p => String(p.id) === String(processId));
        if (index > -1) levelArray.splice(index, 1);
    });
    if (typeof updateProcessSummary === 'function') updateProcessSummary();
}

// Surface an APQC load failure to the user: the picker must never look like an
// empty result set when the request actually failed.
function handleAPQCLoadFailure(selectEl, label, detail) {
    const message = `Could not load ${label}: ${detail}`;
    if (selectEl) {
        selectEl.innerHTML = '';
        const option = document.createElement('option');
        option.value = '';
        option.disabled = true;
        option.textContent = message;
        selectEl.appendChild(option);
    }
    if (typeof showToast === 'function') {
        showToast(message, 'error');
    } else if (window.Platform && Platform.toast) {
        Platform.toast.error(message);
    }
}

// Load Level 1 APQC Processes (Categories)
async function loadAPQCL1Processes() {
  const l1Select = document.getElementById('apqc-l1-processes');
  try {
    const response = await fetch('/dashboard/api/apqc-processes?level=1', {
      credentials: 'same-origin',
      headers: {'X-Requested-With': 'XMLHttpRequest'}
    });

    if (response.ok) {
      const processes = await response.json();
      l1Select.innerHTML = '';

      processes.forEach(proc => {
        const option = document.createElement('option');
        option.value = proc.id;
        option.textContent = `${proc.process_code} - ${proc.process_name}`;
        option.title = proc.process_description || '';
        l1Select.appendChild(option);
      });
    } else {
      handleAPQCLoadFailure(l1Select, 'Level 1 categories', `HTTP ${response.status}`);
    }
  } catch (error) {
    handleAPQCLoadFailure(l1Select, 'Level 1 categories', error.message);
  }
}

// Handle L1 Process Selection Change
function onAPQCL1Change() {
  const l1Select = document.getElementById('apqc-l1-processes');
  selectedL1Processes = Array.from(l1Select.selectedOptions).map(opt => ({
    id: opt.value,
    name: opt.textContent
  }));

  loadAPQCL2Processes();
  updateProcessSummary();
}

// Load Level 2 APQC Processes (Process Groups)
async function loadAPQCL2Processes() {
  const l2Select = document.getElementById('apqc-l2-processes');

  if (selectedL1Processes.length === 0) {
    l2Select.innerHTML = '<option value="">Select L1 categories first...</option>';
    return;
  }

  try {
    const l1Ids = selectedL1Processes.map(p => p.id);
    const response = await fetch('/dashboard/api/apqc-processes/children', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: JSON.stringify({ parent_ids: l1Ids, target_level: 2 })
    });

    if (response.ok) {
      const processes = await response.json();
      l2Select.innerHTML = '';

      processes.forEach(proc => {
        const option = document.createElement('option');
        option.value = proc.id;
        option.textContent = `${proc.process_code} - ${proc.process_name}`;
        option.title = proc.process_description || '';
        l2Select.appendChild(option);
      });
    } else {
      handleAPQCLoadFailure(l2Select, 'Level 2 process groups', `HTTP ${response.status}`);
    }
  } catch (error) {
    handleAPQCLoadFailure(l2Select, 'Level 2 process groups', error.message);
  }
}

// Handle L2 Process Selection Change
function onAPQCL2Change() {
  const l2Select = document.getElementById('apqc-l2-processes');
  selectedL2Processes = Array.from(l2Select.selectedOptions).map(opt => ({
    id: opt.value,
    name: opt.textContent
  }));

  loadAPQCL3Processes();
  updateProcessSummary();
}

// Load Level 3 APQC Processes (Processes)
async function loadAPQCL3Processes() {
  const l3Select = document.getElementById('apqc-l3-processes');

  if (selectedL2Processes.length === 0) {
    l3Select.innerHTML = '<option value="">Select L2 groups first...</option>';
    return;
  }

  try {
    const l2Ids = selectedL2Processes.map(p => p.id);
    const response = await fetch('/dashboard/api/apqc-processes/children', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: JSON.stringify({ parent_ids: l2Ids, target_level: 3 })
    });

    if (response.ok) {
      const processes = await response.json();
      l3Select.innerHTML = '';

      processes.forEach(proc => {
        const option = document.createElement('option');
        option.value = proc.id;
        option.textContent = `${proc.process_code} - ${proc.process_name}`;
        option.title = proc.process_description || '';
        l3Select.appendChild(option);
      });
    } else {
      handleAPQCLoadFailure(l3Select, 'Level 3 processes', `HTTP ${response.status}`);
    }
  } catch (error) {
    handleAPQCLoadFailure(l3Select, 'Level 3 processes', error.message);
  }
}

// Handle L3 Process Selection Change
function onAPQCL3Change() {
  const l3Select = document.getElementById('apqc-l3-processes');
  selectedL3Processes = Array.from(l3Select.selectedOptions).map(opt => ({
    id: opt.value,
    name: opt.textContent
  }));

  loadAPQCL4Processes();
  updateProcessSummary();
}

// Load Level 4 APQC Processes (Activities)
async function loadAPQCL4Processes() {
  const l4Select = document.getElementById('apqc-l4-processes');

  if (selectedL3Processes.length === 0) {
    l4Select.innerHTML = '<option value="">Select L3 processes first...</option>';
    return;
  }

  try {
    const l3Ids = selectedL3Processes.map(p => p.id);
    const response = await fetch('/dashboard/api/apqc-processes/children', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: JSON.stringify({ parent_ids: l3Ids, target_level: 4 })
    });

    if (response.ok) {
      const processes = await response.json();
      l4Select.innerHTML = '';

      processes.forEach(proc => {
        const option = document.createElement('option');
        option.value = proc.id;
        option.textContent = `${proc.process_code} - ${proc.process_name}`;
        option.title = proc.process_description || '';
        l4Select.appendChild(option);
      });
    } else {
      handleAPQCLoadFailure(l4Select, 'Level 4 activities', `HTTP ${response.status}`);
    }
  } catch (error) {
    handleAPQCLoadFailure(l4Select, 'Level 4 activities', error.message);
  }
}

// Handle L4 Process Selection Change
function onAPQCL4Change() {
  const l4Select = document.getElementById('apqc-l4-processes');
  selectedL4Processes = Array.from(l4Select.selectedOptions).map(opt => ({
    id: opt.value,
    name: opt.textContent
  }));

  loadAPQCL5Processes();
  updateProcessSummary();
}

// Load Level 5 APQC Processes (Tasks)
async function loadAPQCL5Processes() {
  const l5Select = document.getElementById('apqc-l5-processes');

  if (selectedL4Processes.length === 0) {
    l5Select.innerHTML = '<option value="">Select L4 activities first...</option>';
    return;
  }

  try {
    const l4Ids = selectedL4Processes.map(p => p.id);
    const response = await fetch('/dashboard/api/apqc-processes/children', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: JSON.stringify({ parent_ids: l4Ids, target_level: 5 })
    });

    if (response.ok) {
      const processes = await response.json();
      l5Select.innerHTML = '';

      processes.forEach(proc => {
        const option = document.createElement('option');
        option.value = proc.id;
        option.textContent = `${proc.process_code} - ${proc.process_name}`;
        option.title = proc.process_description || '';
        l5Select.appendChild(option);
      });
    } else {
      handleAPQCLoadFailure(l5Select, 'Level 5 tasks', `HTTP ${response.status}`);
    }
  } catch (error) {
    handleAPQCLoadFailure(l5Select, 'Level 5 tasks', error.message);
  }
}

// Update Process Summary Display
function updateProcessSummary() {
  const allProcesses = [
    ...selectedL1Processes,
    ...selectedL2Processes,
    ...selectedL3Processes,
    ...selectedL4Processes,
    ...selectedL5Processes
  ];

  const countSpan = document.getElementById('selected-processes-count');
  const listDiv = document.getElementById('selected-processes-list');

  countSpan.textContent = allProcesses.length;

  if (allProcesses.length > 0) {
    listDiv.innerHTML = allProcesses.map(proc => `
      <div class="flex items-center justify-between p-2 bg-background rounded border border-border">
        <span class="text-sm text-foreground/80">${proc.name}</span>
        <button type="button" data-remove-process="${String(proc.id).replace(/"/g, '&quot;')}" aria-label="Remove selected process" class="text-destructive hover:text-red-800">
          <i data-lucide="x" class="w-3 h-3"></i>
        </button>
      </div>
    `).join('');

    if (typeof lucide !== 'undefined') {
      lucide.createIcons();
    }
  } else {
    listDiv.innerHTML = '<p class="text-sm text-muted-foreground">No processes selected yet</p>';
  }
}

// Clear All Selected Processes
function clearSelectedProcesses() {
  selectedL1Processes = [];
  selectedL2Processes = [];
  selectedL3Processes = [];
  selectedL4Processes = [];
  selectedL5Processes = [];

  document.getElementById('apqc-l1-processes').selectedIndex = -1;
  document.getElementById('apqc-l2-processes').innerHTML = '<option value="">Select L1 categories first...</option>';
  document.getElementById('apqc-l3-processes').innerHTML = '<option value="">Select L2 groups first...</option>';
  document.getElementById('apqc-l4-processes').innerHTML = '<option value="">Select L3 processes first...</option>';
  document.getElementById('apqc-l5-processes').innerHTML = '<option value="">Select L4 activities first...</option>';

  updateProcessSummary();

  // Hide vendor discovery section
  document.getElementById('process-vendor-discovery-section').classList.add('hidden');
}

// Discover Vendors for Selected Processes
async function discoverVendorsForProcesses() {
  const allProcesses = [
    ...selectedL1Processes,
    ...selectedL2Processes,
    ...selectedL3Processes,
    ...selectedL4Processes,
    ...selectedL5Processes
  ];

  if (allProcesses.length === 0) {
    showToast('Please select at least one process', 'error');
    return;
  }

  try {
    const processIds = allProcesses.map(p => parseInt(p.id));

    const response = await fetch('/dashboard/api/vendors/by-processes', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: JSON.stringify({ process_ids: processIds })
    });

    if (response.ok) {
      const vendors = await response.json();
      displayProcessVendors(vendors);
    } else {
      showToast(`Failed to discover vendors (HTTP ${response.status})`, 'error');
    }
  } catch (error) {
    showToast(`Error discovering vendors: ${error.message}`, 'error');
  }
}

// Display Vendors Supporting Selected Processes
function displayProcessVendors(vendors) {
  const section = document.getElementById('process-vendor-discovery-section');
  const listDiv = document.getElementById('process-discovered-vendors-list');

  if (vendors.length === 0) {
    listDiv.innerHTML = '<p class="text-sm text-muted-foreground">No vendors found supporting the selected processes</p>';
  } else {
    listDiv.innerHTML = `
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        ${vendors.map(vendor => `
          <div class="border rounded-lg p-4 hover:bg-muted/50">
            <div class="flex items-start justify-between">
              <div>
                <h6 class="font-semibold text-foreground">${vendor.name}</h6>
                <p class="text-xs text-muted-foreground mt-1">${vendor.product_count || 0} products</p>
              </div>
              <span class="px-2 py-1 bg-primary/10 text-primary text-xs rounded">${Platform.figure.percent(vendor.process_coverage)} coverage</span>
            </div>
            <div class="mt-2 text-sm text-muted-foreground">
              Supports ${vendor.supported_process_count || 0} of ${vendor.total_process_count || 0} selected processes
            </div>
          </div>
        `).join('')}
      </div>
    `;
  }

  section.classList.remove('hidden');

  if (typeof lucide !== 'undefined') {
    lucide.createIcons();
  }
}

// Delegated, because the CSP refuses inline on*= attributes: the per-process
// remove button carried onclick="removeProcess('id')" and never fired.
// removeProcess() is defined at the top of this file and only splices the
// in-memory selectedL1..L5Processes arrays -- it deletes no server-side data.
document.addEventListener('click', function (event) {
  const el = event.target.closest('[data-remove-process]');
  if (!el) return;
  event.preventDefault();
  removeProcess(el.getAttribute('data-remove-process'));
});
