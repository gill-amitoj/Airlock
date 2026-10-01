/**
 * Workflow Orchestration Engine - Dashboard JavaScript
 * Handles API communication, UI updates, and user interactions
 */

// API Configuration
// Opened as a file: talk to the local docker compose API.
// Served by Flask (e.g. the Azure deployment): use the same origin.
const API_BASE_URL = window.location.protocol === 'file:' ? 'http://localhost:5001' : '';

// DOM Elements Cache
const elements = {
    apiStatus: document.getElementById('api-status'),
    dbStatus: document.getElementById('db-status'),
    redisStatus: document.getElementById('redis-status'),
    workflowCount: document.getElementById('workflow-count'),
    executionCount: document.getElementById('execution-count'),
    workflowsList: document.getElementById('workflows-list'),
    executionsList: document.getElementById('executions-list'),
    outputArea: document.getElementById('output-area')
};

// The admin key lives in sessionStorage: it survives reloads but not closing
// the tab. Storage can throw (private mode, blocked site data), so every
// access is guarded and a failure just means "no key".
const API_KEY_STORAGE = 'workflowEngineApiKey';

let apiKeyInMemory = null;

function getApiKey() {
    if (apiKeyInMemory) return apiKeyInMemory;
    try { return sessionStorage.getItem(API_KEY_STORAGE); } catch (e) { return null; }
}

function clearApiKey() {
    apiKeyInMemory = null;
    try { sessionStorage.removeItem(API_KEY_STORAGE); } catch (e) { /* ignore */ }
}

function promptForApiKey() {
    const key = (window.prompt('Enter the admin key to create and run workflows:') || '').trim();
    if (!key) return false;
    apiKeyInMemory = key;
    try { sessionStorage.setItem(API_KEY_STORAGE, key); } catch (e) { /* memory copy still works */ }
    return true;
}

/**
 * Makes an API request with error handling
 * @param {string} endpoint - API endpoint
 * @param {object} options - Fetch options
 * @returns {Promise<object>} - Response data
 */
async function apiRequest(endpoint, options = {}, isRetry = false) {
    try {
        const { headers: extraHeaders, ...fetchOptions } = options;
        const headers = { 'Content-Type': 'application/json', ...extraHeaders };
        const apiKey = getApiKey();
        if (apiKey && fetchOptions.method && fetchOptions.method !== 'GET') {
            headers['X-API-Key'] = apiKey;
        }

        const response = await fetch(`${API_BASE_URL}${endpoint}`, { ...fetchOptions, headers });

        // Writes on a deployed instance need the admin key: ask once, then retry.
        if (response.status === 401 && !isRetry) {
            clearApiKey();
            if (promptForApiKey()) {
                return apiRequest(endpoint, options, true);
            }
            throw new Error('This demo is view-only. Creating and running workflows needs the admin key.');
        }

        if (!response.ok) {
            // Surface the server's explanation when there is one. Validation
            // rejections from the AI endpoint say what was wrong with the
            // generated workflow, which is the useful part for the user.
            let detail = '';
            try {
                const body = await response.json();
                if (body && body.error) {
                    detail = typeof body.error === 'string'
                        ? body.error
                        : (body.error.message || '');
                }
                if (body && body.hint) {
                    detail += ` (${body.hint})`;
                }
            } catch (e) {
                // Non-JSON error body - fall back to the status alone.
            }
            throw new Error(
                detail
                    ? `HTTP error! status: ${response.status} - ${detail}`
                    : `HTTP error! status: ${response.status}`
            );
        }

        return await response.json();
    } catch (error) {
        console.error(`API Error (${endpoint}):`, error);
        throw error;
    }
}

/**
 * Updates the health status indicators
 * @param {object} health - Health check response
 */
function updateHealthStatus(health) {
    elements.apiStatus.textContent = health.status === 'healthy' ? '✓ Online' : '✗ Offline';
    elements.dbStatus.textContent = health.database === 'healthy' ? '✓ Connected' : '✗ Error';
    elements.redisStatus.textContent = health.redis === 'healthy' ? '✓ Connected' : '✗ Error';
}

/**
 * Renders a workflow card
 * @param {object} workflow - Workflow data
 * @returns {string} - HTML string
 */
function renderWorkflowCard(workflow) {
    const stepsHtml = workflow.steps.length > 0 
        ? `<div class="steps-list">
            ${workflow.steps.map((step, index) => `
                <div class="step-item">
                    <span class="step-number">${index + 1}</span>
                    <span><strong>${escapeHtml(step.name)}</strong> → ${escapeHtml(step.task_type)}</span>
                </div>
            `).join('')}
           </div>`
        : '<div class="meta" style="margin-top:10px;">No steps defined</div>';
    
    return `
        <div class="workflow-card">
            <h4>${escapeHtml(workflow.name)} <span class="status-badge ${escapeHtml(workflow.status)}">${escapeHtml(workflow.status)}</span></h4>
            <div class="meta">ID: ${escapeHtml(workflow.id)}</div>
            <div class="meta">${escapeHtml(workflow.description) || 'No description'}</div>
            ${stepsHtml}
        </div>
    `;
}

/**
 * Renders an execution card
 * @param {object} execution - Execution data
 * @returns {string} - HTML string
 */
function renderExecutionCard(execution) {
    const startedAt = execution.started_at 
        ? new Date(execution.started_at).toLocaleString() 
        : 'Pending';
    
    const completedHtml = execution.status === 'completed' 
        ? `<div class="meta" style="color:#00ff88;">✓ Completed at ${new Date(execution.completed_at).toLocaleString()}</div>`
        : '';
    
    const errorHtml = execution.error_message 
        ? `<div class="meta error">Error: ${escapeHtml(execution.error_message)}</div>`
        : '';
    
    return `
        <div class="execution-card">
            <h4>Execution <span class="status-badge ${escapeHtml(execution.status)}">${escapeHtml(execution.status)}</span></h4>
            <div class="meta">ID: ${escapeHtml(execution.id)}</div>
            <div class="meta">Started: ${escapeHtml(startedAt)}</div>
            ${completedHtml}
            ${errorHtml}
        </div>
    `;
}

/**
 * Escapes HTML to prevent XSS
 * @param {string} text - Text to escape
 * @returns {string} - Escaped text
 */
function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    // Quotes too, so the result is also safe inside attribute values.
    return String(text).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

/**
 * Loads all dashboard data from the API
 */
async function loadData() {
    try {
        // Health check
        const health = await apiRequest('/health');
        updateHealthStatus(health);

        // Load workflows
        const workflows = await apiRequest('/api/v1/workflows');
        elements.workflowCount.textContent = workflows.count;
        
        if (workflows.workflows.length === 0) {
            elements.workflowsList.innerHTML = '<div class="empty-state">No workflows yet. Click "Create Demo Workflow" to get started!</div>';
        } else {
            elements.workflowsList.innerHTML = workflows.workflows.map(renderWorkflowCard).join('');
        }

        // Load executions
        const executions = await apiRequest('/api/v1/executions');
        elements.executionCount.textContent = executions.count;
        
        if (executions.executions.length === 0) {
            elements.executionsList.innerHTML = '<div class="empty-state">No executions yet. Run a workflow to see results!</div>';
        } else {
            elements.executionsList.innerHTML = executions.executions
                .slice(0, 5)
                .map(renderExecutionCard)
                .join('');
        }
    } catch (error) {
        elements.apiStatus.textContent = '✗ Offline';
        elements.apiStatus.style.color = '#ff4444';
        console.error('Failed to load data:', error);
    }
}

/**
 * Creates a demo workflow with a sample step
 */
async function createDemoWorkflow() {
    const output = elements.outputArea;
    output.innerHTML = '<div class="output-box">Creating workflow...</div>';
    
    try {
        // Create workflow
        const workflow = await apiRequest('/api/v1/workflows', {
            method: 'POST',
            body: JSON.stringify({ 
                name: 'demo-' + Date.now(), 
                description: 'Demo workflow created from dashboard' 
            })
        });
        
        // Add a step
        await apiRequest(`/api/v1/workflows/${workflow.id}/steps`, {
            method: 'POST',
            body: JSON.stringify({ 
                name: 'fetch_joke', 
                task_type: 'http_request', 
                step_order: 0,
                config: { 
                    url: 'https://official-joke-api.appspot.com/random_joke', 
                    method: 'GET' 
                }
            })
        });
        
        // Activate workflow
        await apiRequest(`/api/v1/workflows/${workflow.id}/activate`, {
            method: 'POST'
        });
        
        output.innerHTML = `<div class="output-box">✓ Created workflow: ${escapeHtml(workflow.name)}\n✓ Added step: fetch_joke\n✓ Activated!\n\nWorkflow ID: ${escapeHtml(workflow.id)}</div>`;
        
        // Refresh the dashboard
        await loadData();
    } catch (error) {
        output.innerHTML = `<div class="output-box error">Error: ${escapeHtml(error.message)}</div>`;
    }
}

/**
 * Runs the most recent active workflow
 */
async function runWorkflow() {
    const output = elements.outputArea;
    output.innerHTML = '<div class="output-box">Finding workflow to run...</div>';
    
    try {
        // Get active workflows
        const workflows = await apiRequest('/api/v1/workflows?status=active');
        
        if (workflows.workflows.length === 0) {
            output.innerHTML = '<div class="output-box">No active workflows. Create one first!</div>';
            return;
        }
        
        const workflow = workflows.workflows[0];
        output.innerHTML = `<div class="output-box">Running workflow: ${escapeHtml(workflow.name)}...</div>`;
        
        // Create execution
        const execution = await apiRequest('/api/v1/executions', {
            method: 'POST',
            body: JSON.stringify({ 
                workflow_id: workflow.id, 
                idempotency_key: 'run-' + Date.now() 
            })
        });
        
        // Poll for completion
        let result = execution;
        for (let i = 0; i < 10; i++) {
            await sleep(1000);
            result = await apiRequest(`/api/v1/executions/${execution.id}`);
            
            if (result.status === 'completed' || result.status === 'failed') {
                break;
            }
        }
        
        output.innerHTML = `<div class="output-box">Workflow: ${escapeHtml(workflow.name)}\nStatus: ${escapeHtml(result.status.toUpperCase())}\n\nOutput:\n${escapeHtml(JSON.stringify(result.output_data, null, 2))}</div>`;
        
        // Refresh the dashboard
        await loadData();
    } catch (error) {
        output.innerHTML = `<div class="output-box error">Error: ${escapeHtml(error.message)}</div>`;
    }
}

/**
 * Sleep utility function
 * @param {number} ms - Milliseconds to sleep
 * @returns {Promise} - Resolves after delay
 */
function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Workflow configurations for different demo types
 */
const WORKFLOW_CONFIGS = {
    joke: {
        name: 'joke-workflow',
        description: 'Fetches a random joke from the internet',
        steps: [
            {
                name: 'fetch_joke',
                task_type: 'http_request',
                config: {
                    url: 'https://official-joke-api.appspot.com/random_joke',
                    method: 'GET'
                }
            }
        ]
    },
    user: {
        name: 'user-workflow',
        description: 'Fetches fake user data',
        steps: [
            {
                name: 'fetch_user',
                task_type: 'http_request',
                config: {
                    url: 'https://jsonplaceholder.typicode.com/users/1',
                    method: 'GET'
                }
            }
        ]
    },
    cat: {
        name: 'cat-fact-workflow',
        description: 'Fetches a random cat fact',
        steps: [
            {
                name: 'fetch_cat_fact',
                task_type: 'http_request',
                config: {
                    url: 'https://catfact.ninja/fact',
                    method: 'GET'
                }
            }
        ]
    },
    todo: {
        name: 'todo-workflow',
        description: 'Fetches a todo item',
        steps: [
            {
                name: 'fetch_todo',
                task_type: 'http_request',
                config: {
                    url: 'https://jsonplaceholder.typicode.com/todos/1',
                    method: 'GET'
                }
            }
        ]
    },
    multi: {
        name: 'multi-step-workflow',
        description: 'Multi-step: Fetches joke, user, and combines them',
        steps: [
            {
                name: 'step1_fetch_joke',
                task_type: 'http_request',
                config: {
                    url: 'https://official-joke-api.appspot.com/random_joke',
                    method: 'GET'
                }
            },
            {
                name: 'step2_fetch_user',
                task_type: 'http_request',
                config: {
                    url: 'https://jsonplaceholder.typicode.com/users/1',
                    method: 'GET'
                }
            },
            {
                name: 'step3_fetch_post',
                task_type: 'http_request',
                config: {
                    url: 'https://jsonplaceholder.typicode.com/posts/1',
                    method: 'GET'
                }
            }
        ]
    }
};

/**
 * Creates and runs a workflow of the specified type
 * @param {string} type - Type of workflow (joke, user, cat, todo, multi)
 */
async function createAndRunWorkflow(type) {
    const output = elements.outputArea;
    const config = WORKFLOW_CONFIGS[type];
    
    if (!config) {
        output.innerHTML = '<div class="output-box error">Unknown workflow type!</div>';
        return;
    }
    
    const workflowName = `${escapeHtml(config.name)}-${Date.now()}`;
    
    output.innerHTML = `<div class="output-box">Creating <strong>${escapeHtml(config.name)}</strong>...<br>Steps: ${config.steps.length}</div>`;
    
    try {
        // Step 1: Create workflow
        const workflow = await apiRequest('/api/v1/workflows', {
            method: 'POST',
            body: JSON.stringify({ 
                name: workflowName, 
                description: config.description 
            })
        });
        output.innerHTML = `<div class="output-box">Workflow <strong>${escapeHtml(workflowName)}</strong> created.<br>Adding steps...</div>`;

        // Step 2: Add all steps
        for (let i = 0; i < config.steps.length; i++) {
            const step = config.steps[i];
            await apiRequest(`/api/v1/workflows/${workflow.id}/steps`, {
                method: 'POST',
                body: JSON.stringify({ 
                    name: step.name, 
                    task_type: step.task_type, 
                    step_order: i,
                    config: step.config
                })
            });
            output.innerHTML = `<div class="output-box">Workflow <strong>${escapeHtml(workflowName)}</strong> created.<br>Step ${i+1} of ${config.steps.length}: <strong>${escapeHtml(step.name)}</strong> added.<br>${i < config.steps.length - 1 ? 'Adding next step...' : 'Activating workflow...'}</div>`;
        }

        // Step 3: Activate workflow
        await apiRequest(`/api/v1/workflows/${workflow.id}/activate`, {
            method: 'POST'
        });
        output.innerHTML = `<div class="output-box">Workflow <strong>${escapeHtml(workflowName)}</strong> activated.<br>Running workflow now...</div>`;

        // Step 4: Execute workflow
        const execution = await apiRequest('/api/v1/executions', {
            method: 'POST',
            body: JSON.stringify({ 
                workflow_id: workflow.id, 
                idempotency_key: 'run-' + Date.now() 
            })
        });

        // Step 5: Poll for completion
        let result = execution;
        for (let i = 0; i < 15; i++) {
            await sleep(1000);
            result = await apiRequest(`/api/v1/executions/${execution.id}`);
            if (result.status === 'completed' || result.status === 'failed') {
                break;
            }
            output.innerHTML = `<div class="output-box">Workflow <strong>${escapeHtml(workflowName)}</strong> is running... (${i+1}s)</div>`;
        }

        // Step 6: Show result
        let statusText = result.status === 'completed' ? 'Success!' : 'Failed';
        output.innerHTML = `<div class="output-box"><strong>${statusText}</strong> Workflow: <strong>${escapeHtml(workflowName)}</strong><br>Status: <strong>${escapeHtml(result.status.toUpperCase())}</strong><br><br>Output:<br><pre>${escapeHtml(JSON.stringify(result.output_data, null, 2))}</pre></div>`;

        // Refresh the dashboard
        await loadData();
    } catch (error) {
        output.innerHTML = `<div class="output-box error">Error: ${escapeHtml(error.message)}</div>`;
    }
}

// Steps from the most recent AI generation, held here so they are passed to the
// run handler by reference rather than being serialized through HTML.
let lastGeneratedSteps = null;
let lastGeneratedPrompt = '';

/**
 * Generates and optionally runs a workflow using AI
 */
async function generateAIWorkflow() {
    const promptInput = document.getElementById('ai-prompt');
    const output = document.getElementById('ai-output-area') || elements.outputArea;
    const prompt = promptInput.value.trim();
    
    if (!prompt) {
        output.innerHTML = '<div class="output-box error">Please enter a description of what you want to automate.</div>';
        return;
    }
    
    output.innerHTML = '<div class="output-box">🤖 AI is thinking...</div>';
    
    try {
        // Call the AI endpoint
        const result = await apiRequest('/api/v1/ai/generate-workflow', {
            method: 'POST',
            body: JSON.stringify({ prompt })
        });
        
        if (!result.success || !result.steps || result.steps.length === 0) {
            output.innerHTML = `<div class="output-box error">AI couldn't generate steps. Try a simpler prompt.</div>`;
            return;
        }
        
        const steps = result.steps;
        
        // Show the generated steps
        // Hold the steps in JS rather than serializing them into markup. Model
        // output is untrusted, so it must never be parsed as HTML or as JS source.
        lastGeneratedSteps = steps;
        lastGeneratedPrompt = prompt;

        // Every interpolated field here is model-controlled, so all of it goes
        // through escapeHtml and lands as text.
        const stepsHtml = steps.map((s, i) =>
            `<div class="step-item"><span class="step-number">${i+1}</span> <strong>${escapeHtml(s.name)}</strong>: ${escapeHtml(s.description || s.task_type)}</div>`
        ).join('');

        output.innerHTML = `
            <div class="output-box">
                <strong>AI Generated ${steps.length} step(s):</strong><br><br>
                ${stepsHtml}
                <br><br>
                <button class="demo-btn" id="run-ai-workflow-btn" style="margin-top:10px;">
                    ▶ Run This Workflow
                </button>
            </div>
        `;

        // Attach the handler to the element instead of embedding a call in an
        // attribute, so the steps are passed by reference and never stringified.
        const runBtn = document.getElementById('run-ai-workflow-btn');
        if (runBtn) {
            runBtn.addEventListener('click', () => {
                runAIGeneratedWorkflow(lastGeneratedSteps, lastGeneratedPrompt);
            });
        }

    } catch (error) {
        if (error.message.includes('503') || error.message.includes('Connection')) {
            output.innerHTML = `<div class="output-box error">AI model is not enabled on this setup right now. You can still use all the demo workflows above without AI.</div>`;
        } else {
            output.innerHTML = `<div class="output-box error">Error: ${escapeHtml(error.message)}</div>`;
        }
    }
}

/**
 * Runs a workflow generated by AI
 * @param {Array} steps - Array of step configurations
 * @param {string} description - Original prompt description
 */
async function runAIGeneratedWorkflow(steps, description) {
    const output = document.getElementById('ai-output-area') || elements.outputArea;
    const workflowName = `ai-workflow-${Date.now()}`;
    
    output.innerHTML = `<div class="output-box">Creating workflow from AI steps...</div>`;
    
    try {
        // Step 1: Create workflow
        const workflow = await apiRequest('/api/v1/workflows', {
            method: 'POST',
            body: JSON.stringify({ 
                name: workflowName, 
                description: `AI Generated: ${description}`
            })
        });
        
        // Step 2: Add all steps
        for (let i = 0; i < steps.length; i++) {
            const step = steps[i];
            await apiRequest(`/api/v1/workflows/${workflow.id}/steps`, {
                method: 'POST',
                body: JSON.stringify({ 
                    name: step.name, 
                    task_type: step.task_type || 'http_request', 
                    step_order: i,
                    config: step.config
                })
            });
            output.innerHTML = `<div class="output-box">Added step ${i+1}/${steps.length}: <strong>${escapeHtml(step.name)}</strong></div>`;
        }

        // Step 3: Activate workflow
        await apiRequest(`/api/v1/workflows/${workflow.id}/activate`, {
            method: 'POST'
        });
        output.innerHTML = `<div class="output-box">Workflow activated. Running...</div>`;

        // Step 4: Execute workflow
        const execution = await apiRequest('/api/v1/executions', {
            method: 'POST',
            body: JSON.stringify({ 
                workflow_id: workflow.id, 
                idempotency_key: 'ai-run-' + Date.now() 
            })
        });

        // Step 5: Poll for completion
        let result = execution;
        for (let i = 0; i < 20; i++) {
            await sleep(1000);
            result = await apiRequest(`/api/v1/executions/${execution.id}`);
            if (result.status === 'completed' || result.status === 'failed') {
                break;
            }
            output.innerHTML = `<div class="output-box">Running... (${i+1}s)</div>`;
        }

        // Step 6: Show result
        const statusEmoji = result.status === 'completed' ? '✅' : '❌';
        output.innerHTML = `
            <div class="output-box">
                <strong>${statusEmoji} ${escapeHtml(result.status.toUpperCase())}</strong><br>
                Workflow: <strong>${escapeHtml(workflowName)}</strong><br><br>
                <strong>Output:</strong><br>
                <pre>${escapeHtml(JSON.stringify(result.output_data, null, 2))}</pre>
            </div>
        `;

        // Refresh the dashboard
        await loadData();
    } catch (error) {
        output.innerHTML = `<div class="output-box error">Error: ${escapeHtml(error.message)}</div>`;
    }
}

/**
 * Initialize the dashboard
 */
function init() {
    // Load initial data
    loadData();
    
    // Set up auto-refresh every 10 seconds
    setInterval(loadData, 10000);
    
    // Handlers are attached here rather than as inline onclick attributes, so
    // the Content-Security-Policy can forbid inline script entirely.
    document.querySelectorAll('[data-demo]').forEach(btn => {
        btn.addEventListener('click', () => createAndRunWorkflow(btn.dataset.demo));
    });
    document.getElementById('ai-generate-btn').addEventListener('click', generateAIWorkflow);
    document.getElementById('ai-prompt').addEventListener('keydown', event => {
        if (event.key === 'Enter') generateAIWorkflow();
    });
    document.getElementById('refresh-btn').addEventListener('click', loadData);
}

// Start the application when DOM is ready
document.addEventListener('DOMContentLoaded', init);
