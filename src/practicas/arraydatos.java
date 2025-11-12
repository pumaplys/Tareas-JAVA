package practicas;

import java.util.Arrays;

public class arraydatos {
    public static void main(String[] args) {
        String[] datosF = {"Felipe", "Garcia", "Avd. la palmera 1"};
        String[] datosL = {"Laura", "Rogdriguez", "Calle del Chopo 43"};
        String[] datosA = {"Abraham", "Moreno", "El grande S/N"};

        System.out.println(Arrays.toString(datosF));
        System.out.println(Arrays.toString(datosL));
        System.out.println(Arrays.toString(datosA));

        String[][] alumnos = {datosF, datosL, datosA};
        System.out.println("Vamos a imprimir el array de arrays");
        for (String[] alumno : alumnos) {
            System.out.println(Arrays.toString(alumno));
        }
    }
}
