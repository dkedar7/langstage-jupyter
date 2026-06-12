import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { ICommandPalette } from '@jupyterlab/apputils';
import { LabIcon } from '@jupyterlab/ui-components';
import { IFileBrowserFactory } from '@jupyterlab/filebrowser';
import { ChatWidget } from './widget';
import { chatIconSvgstr } from './icons';

// Create a custom chat icon
const chatIcon = new LabIcon({
  name: 'deepagents:chat',
  svgstr: chatIconSvgstr
});

/**
 * The command IDs used by the plugin.
 */
namespace CommandIDs {
  export const openChat = 'deepagents:open-chat';
}

/**
 * Initialization data for the langstage-jupyter extension.
 */
const plugin: JupyterFrontEndPlugin<void> = {
  id: 'langstage-jupyter:plugin',
  description: 'A JupyterLab extension for DeepAgents chat interface',
  autoStart: true,
  optional: [ICommandPalette, IFileBrowserFactory],
  activate: (
    app: JupyterFrontEnd,
    palette: ICommandPalette | null,
    browserFactory: IFileBrowserFactory | null
  ) => {
    console.log('JupyterLab extension langstage-jupyter is activated!');

    // Create widget immediately on startup
    const widget = new ChatWidget(app.shell, browserFactory);
    widget.id = 'deepagents-chat';
    widget.title.label = ''; // Remove label from sidebar, show only icon
    widget.title.icon = chatIcon;
    widget.title.closable = true;
    widget.title.caption = 'Deep Agents'; // Tooltip on hover

    // Add to right sidebar at the bottom (high rank value)
    app.shell.add(widget, 'right', { rank: 1000 });

    // Add command to open chat (useful if user closes the widget)
    app.commands.addCommand(CommandIDs.openChat, {
      label: 'Deep Agents',
      caption: 'Open DeepAgents chat interface',
      icon: chatIcon,
      execute: () => {
        if (!widget.isAttached) {
          app.shell.add(widget, 'right', { rank: 1000 });
        }
        app.shell.activateById(widget.id);
      }
    });

    // Add command to command palette
    if (palette) {
      palette.addItem({
        command: CommandIDs.openChat,
        category: 'Deep Agents'
      });
    }

    console.log('DeepAgents chat interface ready');
  }
};

export default plugin;
