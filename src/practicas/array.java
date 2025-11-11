package practicas;

import java.util.Arrays;

public class array {
    public static void main(String[] args) {
        int notaPrimerExamenEduardo = 7;
        int notaSegundoExamenEduardo = 6;

        int[] notaEduardo = new int[3];
        notaEduardo[0] = 10;
        notaEduardo[1] = 10;
        notaEduardo[2] = 10;

        System.out.println(Arrays.toString(notaEduardo));
        int [] notaEduardoAuxiliar = notaEduardo;
        notaEduardo = new int[4];

        for (int i = 0; i < 3; i++){
            notaEduardo[i] = notaEduardoAuxiliar[i];
        }
        notaEduardo[3] = 9;
        System.out.println(Arrays.toString(notaEduardoAuxiliar));
        System.out.println(Arrays.toString(notaEduardo));
    }
}
