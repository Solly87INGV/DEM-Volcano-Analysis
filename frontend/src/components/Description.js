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
        for rapid and repeatable measurements. Detection parameters are selected deterministically from the volcano type in the
        Smithsonian Global Volcanism Program (GVP) database.
      </p>

      <p>
        <b>Input:</b> a georeferenced DEM (geographic DEMs are reprojected to UTM automatically; the method is validated at 20–30 m resolution,
        e.g. Copernicus GLO-30) and the GVP volcano number (optional, but strongly recommended: it selects the parameter preset and anchors the caldera centre). <br />
        <b>Outputs:</b> base and caldera geometry (area, diameter), caldera depth (p90/p05 and max/min), caldera and edifice volumes,
        a PDF report with plots, and an exportable metrics package (JSON + CSV) for quality control and reproducible downstream analysis.
        The caldera rim can be redrawn or imported (GeoJSON) and the volumes recalculated.
      </p>
    </div>
  );
};

export default Description;