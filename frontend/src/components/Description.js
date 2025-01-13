// Description.js
import React from 'react';
import './Description.css';

const Description = () => {
  return (
    <div className="description-container">
      <h2>Description:</h2>
      <p>
      Geomorphology and geomorphometry of volcanic edifices are powerful instruments for understanding and monitoring the behaviour of volcanoes and their impact on the landscape.
      Volcano morphologies, edifices shape and size, result from the interplay over time of both constructive and destructive geological processes and their underlaying causes.
      The geomorphometry is useful for a quantitative landform mapping and analysis, to measure geometric characteristics of individual landform.
      </p>
      <p>
        We here describe a systematic methodology to extract quantitative morphometric parameters of volcanoes from DEM. 
        In this work we propose the development of a python code for the analysis of volcanic edifices starting from their DEMs and the realization of a JavaScript 
        that is allowed to create a flexible and user-friendly environment to conduct rapid and repeatable analyses.
      </p>
    </div>
  );
};

export default Description;
