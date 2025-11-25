"use strict";
(self["webpackChunkcleon_cell_control"] = self["webpackChunkcleon_cell_control"] || []).push([["lib_index_js"],{

/***/ "./lib/index.js":
/*!**********************!*\
  !*** ./lib/index.js ***!
  \**********************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": () => (__WEBPACK_DEFAULT_EXPORT__)
/* harmony export */ });
/* harmony import */ var _jupyterlab_notebook__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! @jupyterlab/notebook */ "webpack/sharing/consume/default/@jupyterlab/notebook");
/* harmony import */ var _jupyterlab_notebook__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_jupyterlab_notebook__WEBPACK_IMPORTED_MODULE_0__);

const COMM_TARGET = 'cleon_cell_control';
const plugin = {
    id: 'cleon-cell-control:plugin',
    description: 'JupyterLab extension for cell manipulation from kernel',
    autoStart: true,
    requires: [_jupyterlab_notebook__WEBPACK_IMPORTED_MODULE_0__.INotebookTracker],
    activate: (app, tracker) => {
        console.log('cleon-cell-control: activated');
        // Expose global function for buttons to call
        window.cleonInsertAndRun = (code) => {
            const notebook = tracker.currentWidget;
            if (!notebook) {
                console.error('cleon-cell-control: No active notebook');
                return;
            }
            const notebookModel = notebook.content.model;
            if (!notebookModel) {
                console.error('cleon-cell-control: No notebook model');
                return;
            }
            const activeCellIndex = notebook.content.activeCellIndex;
            const sharedModel = notebookModel.sharedModel;
            const newCellIndex = activeCellIndex + 1;
            sharedModel.insertCell(newCellIndex, {
                cell_type: 'code',
                source: code
            });
            notebook.content.activeCellIndex = newCellIndex;
            // Execute the newly inserted cell
            setTimeout(async () => {
                try {
                    await _jupyterlab_notebook__WEBPACK_IMPORTED_MODULE_0__.NotebookActions.run(notebook.content, notebook.sessionContext);
                    console.log('cleon-cell-control: cell executed via button');
                }
                catch (err) {
                    console.error('cleon-cell-control: execution error', err);
                }
            }, 150);
        };
        console.log('cleon-cell-control: window.cleonInsertAndRun registered');
        const registerCommTarget = (kernel) => {
            kernel.registerCommTarget(COMM_TARGET, (comm, openMsg) => {
                console.log('cleon-cell-control: comm opened');
                comm.onMsg = (msg) => {
                    const data = msg.content.data;
                    console.log('cleon-cell-control: received', data);
                    const notebook = tracker.currentWidget;
                    if (!notebook) {
                        comm.send({ status: 'error', message: 'No active notebook' });
                        return;
                    }
                    const notebookModel = notebook.content.model;
                    if (!notebookModel) {
                        comm.send({ status: 'error', message: 'No notebook model' });
                        return;
                    }
                    const activeCell = notebook.content.activeCell;
                    const activeCellIndex = notebook.content.activeCellIndex;
                    try {
                        switch (data.action) {
                            case 'insert_below': {
                                const cellType = data.cell_type || 'code';
                                const sharedModel = notebookModel.sharedModel;
                                const newCell = sharedModel.insertCell(activeCellIndex + 1, {
                                    cell_type: cellType,
                                    source: data.code || ''
                                });
                                notebook.content.activeCellIndex = activeCellIndex + 1;
                                comm.send({ status: 'ok', action: 'insert_below', cell_id: newCell.id });
                                break;
                            }
                            case 'insert_above': {
                                const cellType = data.cell_type || 'code';
                                const sharedModel = notebookModel.sharedModel;
                                const newCell = sharedModel.insertCell(activeCellIndex, {
                                    cell_type: cellType,
                                    source: data.code || ''
                                });
                                comm.send({ status: 'ok', action: 'insert_above', cell_id: newCell.id });
                                break;
                            }
                            case 'replace': {
                                if (activeCell && activeCell.model.sharedModel) {
                                    activeCell.model.sharedModel.source = data.code || '';
                                    comm.send({ status: 'ok', action: 'replace' });
                                }
                                else {
                                    comm.send({ status: 'error', message: 'No active cell' });
                                }
                                break;
                            }
                            case 'execute': {
                                if (notebook.sessionContext.session?.kernel) {
                                    void notebook.content.widgets[activeCellIndex]?.ready.then(() => {
                                        void notebook.sessionContext.session?.kernel?.requestExecute({
                                            code: activeCell?.model.sharedModel.source || ''
                                        });
                                    });
                                    comm.send({ status: 'ok', action: 'execute' });
                                }
                                else {
                                    comm.send({ status: 'error', message: 'No kernel' });
                                }
                                break;
                            }
                            case 'insert_and_run': {
                                const cellType = data.cell_type || 'code';
                                const sharedModel = notebookModel.sharedModel;
                                const newCellIndex = activeCellIndex + 1;
                                sharedModel.insertCell(newCellIndex, {
                                    cell_type: cellType,
                                    source: data.code || ''
                                });
                                notebook.content.activeCellIndex = newCellIndex;
                                // Execute the newly inserted cell using NotebookActions
                                setTimeout(async () => {
                                    try {
                                        await _jupyterlab_notebook__WEBPACK_IMPORTED_MODULE_0__.NotebookActions.run(notebook.content, notebook.sessionContext);
                                        console.log('cleon-cell-control: cell executed');
                                    }
                                    catch (err) {
                                        console.error('cleon-cell-control: execution error', err);
                                    }
                                }, 150);
                                comm.send({ status: 'ok', action: 'insert_and_run' });
                                break;
                            }
                            default:
                                comm.send({ status: 'error', message: `Unknown action: ${data.action}` });
                        }
                    }
                    catch (error) {
                        console.error('cleon-cell-control: error', error);
                        comm.send({ status: 'error', message: String(error) });
                    }
                };
                comm.onClose = () => {
                    console.log('cleon-cell-control: comm closed');
                };
            });
        };
        // Register comm target for current and future kernels
        tracker.forEach((notebook) => {
            const session = notebook.sessionContext.session;
            if (session?.kernel) {
                registerCommTarget(session.kernel);
            }
        });
        tracker.widgetAdded.connect((_, notebook) => {
            notebook.sessionContext.kernelChanged.connect((_, args) => {
                if (args.newValue) {
                    registerCommTarget(args.newValue);
                }
            });
            // Also handle if kernel already exists
            const kernel = notebook.sessionContext.session?.kernel;
            if (kernel) {
                registerCommTarget(kernel);
            }
        });
    }
};
/* harmony default export */ const __WEBPACK_DEFAULT_EXPORT__ = (plugin);


/***/ })

}]);
//# sourceMappingURL=lib_index_js.93dc1766ebce314ce69e.js.map