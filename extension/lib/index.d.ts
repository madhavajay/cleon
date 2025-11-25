import { JupyterFrontEndPlugin } from '@jupyterlab/application';
declare global {
    interface Window {
        cleonInsertAndRun?: (code: string) => void;
    }
}
declare const plugin: JupyterFrontEndPlugin<void>;
export default plugin;
