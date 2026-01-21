// Description.js
import React from 'react';
import './Description.css';

const Description = () => {
  return (
    <div className="description-container">
      <h2>Description:</h2>

      <p>
        Geomorphology and geomorphometry of volcanic edifices are powerful tools for understanding and monitoring volcano behaviour and its impact on the landscape.
        Volcano shape and size result from the interplay, over time, of constructive and destructive geological processes and their underlying causes.
        Geomorphometry supports quantitative landform mapping and analysis by measuring geometric characteristics of individual landforms.
      </p>

      <p>
        Here we present a systematic workflow to extract quantitative morphometric parameters of volcanic edifices from DEMs.
        We developed a Python-based analysis core integrated into a JavaScript user interface to provide a flexible and user-friendly environment
        for rapid and repeatable measurements.
      </p>

      <p>
        <b>Input:</b> a georeferenced DEM (projected/metric CRS recommended). <br />
        <b>Outputs:</b> key morphometrics (e.g., base/caldera geometry, heights, volumes), a PDF report with plots, and an exportable metrics package (JSON + CSV) for QA and machine-learning workflows.
      </p>
    </div>
  );
};

export default Description;