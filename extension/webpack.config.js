/**
 * Custom webpack config
 * Uses fixed filenames instead of contenthash for stable dev builds
 * This gets deep merged with JupyterLab's default config via webpack-merge
 */

module.exports = {
  output: {
    // Override to use fixed filenames without content hashes
    filename: '[name].js',
    chunkFilename: '[name].js'
  }
  // Note: ModuleFederationPlugin filename can't be easily overridden here
  // The remoteEntry file will still have a hash, but that's less problematic
};
